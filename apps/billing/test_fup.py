"""
Tests for FUP monitoring and the price-only packages page.

Monitoring must never touch the network: several tests assert that no
MikroTikJob is ever created and that subscriptions/sessions stay as they were.
"""
import datetime as dt
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing import fup
from apps.billing.models import FUPAlert, Subscription
from apps.core.models import AuditLog, SystemSettings
from apps.customers.models import Customer
from apps.mikrotik.models import InternetSession, MikroTikJob, MikroTikRouter
from apps.packages.models import InternetPackage

MAC = 'AA:BB:CC:DD:EE:01'
MB = 1024 * 1024


def _make_user(username, role_name):
    user = User.objects.create_user(username=username, password='pw12345!', is_staff_account=True)
    role, _ = Role.objects.get_or_create(name=role_name)
    StaffProfile.objects.create(user=user, role=role)
    return user


class FUPBase(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        self.router = MikroTikRouter.objects.create(name='RB941', host='192.168.88.1', username='u', password='p')
        self.pkg = InternetPackage.objects.create(
            name='Day', price=100, duration=1, duration_unit='DAYS', unlimited_data=False, fup_limit_mb=100,
        )
        self.cust = Customer.objects.create(full_name='Wanjiku Mwangi', phone_number='254712345678')
        self.sub = Subscription.objects.create(
            customer=self.cust, package=self.pkg, status=Subscription.Status.ACTIVE,
            activation_time=timezone.now(), expiry_time=timezone.now() + dt.timedelta(hours=5),
            mikrotik_username=MAC,
        )
        self.session = InternetSession.objects.create(
            customer=self.cust, subscription=self.sub, router=self.router,
            mac_address=MAC, mikrotik_username=MAC, login_time=timezone.now(),
        )

    def sample(self, bytes_in, bytes_out, mac=MAC):
        return fup.record_usage_samples([{'mac_address': mac, 'bytes_in': bytes_in, 'bytes_out': bytes_out}])

    def use(self, total_mb):
        """Move the recorded total to total_mb with one more sample (counters are cumulative)."""
        self.session.refresh_from_db()
        have = self.session.last_counter_out or 0
        self.sample(self.session.last_counter_in or 0, have + int(total_mb * MB) - (self.session.bytes_uploaded + self.session.bytes_downloaded))


class ClassifyTests(TestCase):
    def test_band_boundaries(self):
        cases = [(0, fup.NORMAL), (69.99, fup.NORMAL), (70, fup.APPROACHING), (84.99, fup.APPROACHING),
                 (85, fup.HIGH), (99.99, fup.HIGH), (100, fup.REACHED), (250, fup.REACHED)]
        for percent, expected in cases:
            self.assertEqual(fup.classify(percent), expected, percent)

    def test_no_limit_has_no_percent(self):
        self.assertIsNone(fup.percent_used(5 * MB, None))
        self.assertIsNone(fup.percent_used(5 * MB, 0))


class UsageRecordingTests(FUPBase):
    def test_first_sample_is_baseline_only(self):
        self.sample(5 * MB, 50 * MB)
        self.session.refresh_from_db()
        self.assertEqual((self.session.bytes_uploaded, self.session.bytes_downloaded), (0, 0))
        self.assertIsNotNone(self.session.last_usage_at)

    def test_later_samples_add_only_the_difference(self):
        self.sample(5 * MB, 50 * MB)
        self.sample(7 * MB, 60 * MB)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bytes_uploaded, 2 * MB)
        self.assertEqual(self.session.bytes_downloaded, 10 * MB)

    def test_resending_the_same_counters_adds_nothing(self):
        self.sample(1 * MB, 10 * MB)
        self.sample(2 * MB, 20 * MB)
        self.sample(2 * MB, 20 * MB)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bytes_downloaded, 10 * MB)

    def test_router_counter_reset_counts_new_traffic(self):
        self.sample(0, 90 * MB)
        self.sample(0, 100 * MB)       # +10 MB
        self.sample(0, 4 * MB)         # router forgot the device and restarted its counter: 4 MB is new
        self.session.refresh_from_db()
        self.assertEqual(self.session.bytes_downloaded, 14 * MB)

    def test_unknown_mac_and_garbage_are_ignored(self):
        n = fup.record_usage_samples([
            {'mac_address': 'DE:AD:BE:EF:00:00', 'bytes_in': 1, 'bytes_out': 1},   # not our customer
            {'mac_address': 'nonsense', 'bytes_in': 1, 'bytes_out': 1},
            {'mac_address': MAC, 'bytes_in': 'x', 'bytes_out': 1},
            {'mac_address': MAC, 'bytes_in': -5, 'bytes_out': 1},
            'not-a-dict',
        ])
        self.assertEqual(n, 0)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.last_usage_at)

    def test_closed_session_is_not_credited(self):
        self.session.status = InternetSession.Status.CLOSED
        self.session.save()
        self.assertEqual(self.sample(1, 1), 0)

    def test_usage_sums_across_devices_of_one_subscription(self):
        InternetSession.objects.create(
            customer=self.cust, subscription=self.sub, router=self.router,
            mac_address='AA:BB:CC:DD:EE:02', login_time=timezone.now(),
        )
        self.sample(0, 0)
        self.sample(0, 10 * MB)
        self.sample(0, 0, mac='AA:BB:CC:DD:EE:02')
        self.sample(0, 5 * MB, mac='AA:BB:CC:DD:EE:02')
        up, down = fup.subscription_usage(self.sub)
        self.assertEqual(down, 15 * MB)


class ThresholdAlertTests(FUPBase):
    def setUp(self):
        super().setUp()
        self.sample(0, 0)  # baseline

    def thresholds(self):
        return sorted(FUPAlert.objects.filter(subscription=self.sub).values_list('threshold', flat=True))

    def test_below_70_percent_creates_nothing(self):
        self.sample(0, 69 * MB)
        self.assertEqual(self.thresholds(), [])

    def test_crossing_each_threshold_creates_one_alert(self):
        self.sample(0, 70 * MB)
        self.assertEqual(self.thresholds(), [70])
        self.sample(0, 86 * MB)
        self.assertEqual(self.thresholds(), [70, 85])
        self.sample(0, 100 * MB)
        self.assertEqual(self.thresholds(), [70, 85, 100])

    def test_no_duplicate_for_same_threshold_and_period(self):
        for total in (71, 72, 75, 80, 84):
            self.sample(0, total * MB)
        self.assertEqual(self.thresholds(), [70])
        fup.check_thresholds(self.sub)
        fup.check_thresholds(self.sub)
        self.assertEqual(self.thresholds(), [70])

    def test_jump_records_only_the_highest_crossed(self):
        self.sample(0, 90 * MB)
        self.assertEqual(self.thresholds(), [85])

    def test_lower_threshold_never_added_after_higher(self):
        self.sample(0, 90 * MB)
        fup.check_thresholds(self.sub)
        self.assertEqual(self.thresholds(), [85])

    def test_alert_snapshot(self):
        self.sample(0, 100 * MB)
        alert = FUPAlert.objects.get()
        self.assertEqual((alert.threshold, alert.limit_mb, alert.usage_bytes), (100, 100, 100 * MB))

    def test_package_without_fup_limit_never_alerts(self):
        self.pkg.fup_limit_mb = None
        self.pkg.save()
        self.sample(0, 5000 * MB)
        self.assertEqual(FUPAlert.objects.count(), 0)

    def test_new_subscription_is_a_new_period(self):
        self.sample(0, 75 * MB)
        sub2 = Subscription.objects.create(
            customer=self.cust, package=self.pkg, status=Subscription.Status.ACTIVE,
            activation_time=timezone.now(), expiry_time=timezone.now() + dt.timedelta(hours=5),
        )
        FUPAlert.objects.create(subscription=sub2, threshold=70, usage_bytes=1, limit_mb=100)  # allowed: different period
        self.assertEqual(FUPAlert.objects.count(), 2)

    def test_monitoring_never_changes_access_or_queues_router_jobs(self):
        before = (self.sub.status, self.sub.expiry_time, self.session.status)
        self.sample(0, 500 * MB)   # far past 100%
        self.sub.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual((self.sub.status, self.sub.expiry_time, self.session.status), before)
        self.assertEqual(MikroTikJob.objects.count(), 0)


class DashboardRowTests(FUPBase):
    def test_active_subscription_without_router_data_is_not_online_or_estimated(self):
        row = fup.dashboard_rows()[0]
        self.assertFalse(row['online'])
        self.assertFalse(row['has_data'])
        self.assertEqual(row['used'], 0)

    def test_online_comes_only_from_recent_router_report(self):
        self.sample(0, 0)
        self.assertTrue(fup.dashboard_rows()[0]['online'])
        InternetSession.objects.update(last_usage_at=timezone.now() - dt.timedelta(minutes=10))
        row = fup.dashboard_rows()[0]
        self.assertFalse(row['online'])
        self.assertTrue(row['has_data'])

    def test_expired_subscription_not_listed(self):
        Subscription.objects.update(expiry_time=timezone.now() - dt.timedelta(minutes=1))
        self.assertEqual(fup.dashboard_rows(), [])

    def test_row_percent_and_status(self):
        self.sample(0, 0)
        self.sample(0, 90 * MB)
        row = fup.dashboard_rows()[0]
        self.assertEqual(row['status'], fup.HIGH)
        self.assertAlmostEqual(row['percent'], 90.0)

    def test_package_without_limit_shows_usage_but_no_status_band(self):
        self.pkg.fup_limit_mb = None
        self.pkg.save()
        self.sample(0, 0)
        self.sample(0, 10 * MB)
        row = fup.dashboard_rows()[0]
        self.assertEqual(row['status'], fup.NO_LIMIT)
        self.assertEqual(row['used'], 10 * MB)


class HeartbeatIntegrationTests(FUPBase):
    def setUp(self):
        super().setUp()
        from django.conf import settings
        self.key = 'test-agent-key'
        self._old = getattr(settings, 'MIKROTIK_AGENT_API_KEY', None)
        settings.MIKROTIK_AGENT_API_KEY = self.key

    def tearDown(self):
        from django.conf import settings
        settings.MIKROTIK_AGENT_API_KEY = self._old

    def beat(self, body):
        return self.client.post('/api/mikrotik/heartbeat/', data=json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {self.key}')

    def test_heartbeat_with_usage_records_it(self):
        self.assertEqual(self.beat({'host_usage': [{'mac_address': MAC, 'bytes_in': 1, 'bytes_out': 10}]}).status_code, 200)
        self.assertEqual(self.beat({'host_usage': [{'mac_address': MAC, 'bytes_in': 2, 'bytes_out': 30}]}).status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual((self.session.bytes_uploaded, self.session.bytes_downloaded), (1, 20))

    def test_old_style_heartbeat_still_works(self):
        self.assertEqual(self.beat({'active_users': [], 'active_sessions': []}).status_code, 200)

    def test_bad_usage_payload_never_fails_the_heartbeat(self):
        self.assertEqual(self.beat({'host_usage': 'garbage'}).status_code, 200)
        self.assertEqual(self.beat({'host_usage': [None, 5, {}]}).status_code, 200)


class FUPPageTests(FUPBase):
    def setUp(self):
        super().setUp()
        self.admin = _make_user('boss', 'SUPER_ADMIN')
        self.sh = _make_user('sh1', 'SHAREHOLDER')

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse('core:fup_monitor')).status_code, 302)

    def test_shareholder_and_admin_can_view(self):
        self.sample(0, 0)
        self.sample(0, 75 * MB)
        for user in (self.admin, self.sh):
            self.client.force_login(user)
            resp = self.client.get(reverse('core:fup_monitor'))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'Wanjiku Mwangi')
            self.assertContains(resp, 'Approaching FUP')

    def test_warns_when_no_data_has_ever_arrived(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('core:fup_monitor')), 'No usage data received yet')

    def test_filters(self):
        self.sample(0, 0)
        self.sample(0, 75 * MB)
        self.client.force_login(self.admin)
        approaching = self.client.get(reverse('core:fup_monitor') + '?filter=APPROACHING')
        reached = self.client.get(reverse('core:fup_monitor') + '?filter=REACHED')
        online = self.client.get(reverse('core:fup_monitor') + '?filter=online')
        self.assertEqual(len(approaching.context['rows']), 1)
        self.assertEqual(len(reached.context['rows']), 0)   # (her 70% warning still lists under Recent warnings)
        self.assertEqual(len(online.context['rows']), 1)


class PackagePriceTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        self.admin = _make_user('boss', 'SUPER_ADMIN')
        self.sh = _make_user('sh1', 'SHAREHOLDER')
        self.pkg = InternetPackage.objects.create(
            name='6 Hours', price=Decimal('30.00'), duration=6, duration_unit='HOURS',
            download_speed_kbps=5000, upload_speed_kbps=2000, device_limit=2,
        )
        self.url = reverse('core:update_package_price', args=[self.pkg.id])

    def test_everyone_on_staff_can_see_packages(self):
        for user in (self.admin, self.sh):
            self.client.force_login(user)
            resp = self.client.get(reverse('core:packages'))
            self.assertContains(resp, '6 Hours')
        self.assertEqual(self.client.get(reverse('core:packages')).status_code, 200)

    def test_only_main_admin_sees_the_price_form(self):
        self.client.force_login(self.sh)
        self.assertNotContains(self.client.get(reverse('core:packages')), self.url)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('core:packages')), self.url)

    def test_main_admin_changes_price_and_it_is_audited(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'price': '45.50'})
        self.pkg.refresh_from_db()
        self.assertEqual(self.pkg.price, Decimal('45.50'))
        log = AuditLog.objects.get(action='PACKAGE_PRICE_CHANGE')
        self.assertEqual(log.actor, self.admin)
        self.assertEqual(log.previous_value['price'], '30.00')
        self.assertEqual(log.new_value['price'], '45.50')

    def test_shareholder_cannot_change_price(self):
        self.client.force_login(self.sh)
        self.assertEqual(self.client.post(self.url, {'price': '1.00'}).status_code, 403)
        self.pkg.refresh_from_db()
        self.assertEqual(self.pkg.price, Decimal('30.00'))
        self.assertFalse(AuditLog.objects.filter(action='PACKAGE_PRICE_CHANGE').exists())

    def test_time_and_other_fields_cannot_be_changed_through_this_endpoint(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {
            'price': '40', 'duration': '99', 'duration_unit': 'DAYS', 'download_speed_kbps': '1',
            'device_limit': '9', 'is_active': '', 'name': 'Hacked',
        })
        self.pkg.refresh_from_db()
        self.assertEqual(self.pkg.price, Decimal('40.00'))
        self.assertEqual((self.pkg.duration, self.pkg.duration_unit, self.pkg.download_speed_kbps,
                          self.pkg.device_limit, self.pkg.is_active, self.pkg.name),
                         (6, 'HOURS', 5000, 2, True, '6 Hours'))

    def test_invalid_prices_are_rejected(self):
        self.client.force_login(self.admin)
        for bad in ('', 'abc', '0', '-5', '10.999', '100001', 'NaN', 'Infinity'):
            self.client.post(self.url, {'price': bad})
        self.pkg.refresh_from_db()
        self.assertEqual(self.pkg.price, Decimal('30.00'))
        self.assertFalse(AuditLog.objects.filter(action='PACKAGE_PRICE_CHANGE').exists())

    def test_unchanged_price_writes_no_audit_entry(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'price': '30'})
        self.assertFalse(AuditLog.objects.filter(action='PACKAGE_PRICE_CHANGE').exists())

    def test_get_not_allowed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)
