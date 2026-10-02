"""
Tests for the shareholder time-adjustment limits, the request-more flow,
the Main Admin's date/time adjustment, and the customer column on the log.
"""
import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing.models import Subscription
from apps.core.models import AuditLog, SystemSettings, TimeAdjustmentRequest
from apps.customers.models import Customer
from apps.packages.models import InternetPackage


def _make_user(username, role_name):
    user = User.objects.create_user(username=username, password='pw12345!', is_staff_account=True)
    role, _ = Role.objects.get_or_create(name=role_name)
    StaffProfile.objects.create(user=user, role=role)
    return user


class TimeAdjustmentTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        self.admin = _make_user('boss', 'SUPER_ADMIN')
        self.sh = _make_user('sh1', 'SHAREHOLDER')
        pkg = InternetPackage.objects.create(name='Day', price=100, duration=1, duration_unit='DAYS')
        self.cust = Customer.objects.create(full_name='Wanjiku Mwangi', phone_number='254712345678')
        self.sub = Subscription.objects.create(
            customer=self.cust, package=pkg, status=Subscription.Status.ACTIVE,
            activation_time=timezone.now(), expiry_time=timezone.now() + dt.timedelta(hours=1),
        )
        self.add_url = reverse('core:add_subscription_time', args=[self.sub.id])

    def _add(self, minutes=10, reason='Electricity'):
        return self.client.post(self.add_url, {'minutes': minutes, 'reason': reason})

    def _adjustments(self):
        return AuditLog.objects.filter(action='ADMIN_TIME_ADJUSTMENT')

    # ---- shareholder limits -------------------------------------------------
    def test_shareholder_cannot_add_more_than_one_hour(self):
        self.client.force_login(self.sh)
        self._add(minutes=61)
        self.assertEqual(self._adjustments().count(), 0)
        self._add(minutes=60)
        self.assertEqual(self._adjustments().count(), 1)

    def test_shareholder_limited_to_six_per_day(self):
        self.client.force_login(self.sh)
        for _ in range(8):
            self._add(minutes=5)
        self.assertEqual(self._adjustments().filter(actor=self.sh).count(), 6)

    def test_yesterdays_adjustments_do_not_count(self):
        self.client.force_login(self.sh)
        for _ in range(6):
            self._add(minutes=5)
        AuditLog.objects.filter(action='ADMIN_TIME_ADJUSTMENT').update(
            created_at=timezone.now() - dt.timedelta(days=1))
        self._add(minutes=5)
        self.assertEqual(self._adjustments().filter(created_at__date=timezone.localdate()).count(), 1)

    def test_log_stores_the_customer(self):
        self.client.force_login(self.sh)
        self._add(minutes=15)
        details = self._adjustments().get().new_value
        self.assertEqual(details['customer_name'], 'Wanjiku Mwangi')
        self.assertEqual(details['customer_phone'], '254712345678')
        html = self.client.get(reverse('core:time_adjustments_log')).content.decode()
        self.assertIn('Wanjiku Mwangi', html)
        self.assertIn('<th>Customer</th>', html)

    def test_log_shows_customer_for_old_rows_without_it(self):
        AuditLog.objects.create(
            actor=self.sh, action='ADMIN_TIME_ADJUSTMENT', object_type='Subscription',
            object_id=str(self.sub.id), new_value={'minutes_added': 5, 'reason': 'old'})
        self.client.force_login(self.sh)
        self.assertIn('Wanjiku Mwangi', self.client.get(reverse('core:time_adjustments_log')).content.decode())

    # ---- request more -------------------------------------------------------
    def test_request_refused_while_adjustments_remain(self):
        self.client.force_login(self.sh)
        self.client.post(reverse('core:request_more_time_adjustments'))
        self.assertEqual(TimeAdjustmentRequest.objects.count(), 0)

    def test_request_grant_flow(self):
        self.client.force_login(self.sh)
        for _ in range(6):
            self._add(minutes=5)
        page = self.client.get(reverse('core:subscriptions')).content.decode()
        self.assertIn('Request more adjustments', page)
        self.assertIn('6 of 6 used', page)

        self.client.post(reverse('core:request_more_time_adjustments'), {'note': 'busy day'})
        self.client.post(reverse('core:request_more_time_adjustments'))   # duplicate is ignored
        req = TimeAdjustmentRequest.objects.get()
        self.assertEqual(req.status, 'PENDING')
        self.assertIn('waiting for the Company', self.client.get(reverse('core:subscriptions')).content.decode())

        # shareholder cannot decide their own request
        decide = reverse('core:decide_time_adjustment_request', args=[req.id])
        self.assertEqual(self.client.post(decide, {'action': 'approve', 'count': 5}).status_code, 403)

        self.client.force_login(self.admin)
        self.assertIn('Time Requests', self.client.get(reverse('core:dashboard')).content.decode())
        self.assertEqual(self.client.get(reverse('core:time_adjustment_requests_admin')).status_code, 200)
        self.client.post(decide, {'action': 'approve', 'count': 2})
        req.refresh_from_db()
        self.assertEqual((req.status, req.granted_count, req.valid_on), ('APPROVED', 2, timezone.localdate()))

        self.client.force_login(self.sh)
        self._add(minutes=5)
        self._add(minutes=5)
        self._add(minutes=5)   # third one is over the granted 2
        self.assertEqual(self._adjustments().filter(actor=self.sh).count(), 8)

    def test_admin_grant_count_is_validated_and_decline_works(self):
        req = TimeAdjustmentRequest.objects.create(requester=self.sh)
        self.client.force_login(self.admin)
        decide = reverse('core:decide_time_adjustment_request', args=[req.id])
        self.client.post(decide, {'action': 'approve', 'count': 0})
        self.client.post(decide, {'action': 'approve', 'count': 999})
        req.refresh_from_db()
        self.assertEqual(req.status, 'PENDING')
        self.client.post(decide, {'action': 'decline'})
        req.refresh_from_db()
        self.assertEqual(req.status, 'DECLINED')

    # ---- main admin ---------------------------------------------------------
    def test_admin_sets_expiry_with_date_and_time_and_has_no_limit(self):
        self.client.force_login(self.admin)
        target = timezone.localtime(self.sub.expiry_time) + dt.timedelta(days=2, hours=3)
        resp = self.client.post(self.add_url, {
            'expiry_date': target.strftime('%Y-%m-%d'), 'expiry_time': target.strftime('%H:%M'),
            'reason': 'Outage'})
        self.assertEqual(resp.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(timezone.localtime(self.sub.expiry_time).strftime('%Y-%m-%d %H:%M'),
                         target.strftime('%Y-%m-%d %H:%M'))
        self.assertGreater(self._adjustments().get().new_value['minutes_added'], 60)

        for _ in range(8):   # no daily cap for the Company
            later = target + dt.timedelta(hours=1)
            self.client.post(self.add_url, {
                'expiry_date': later.strftime('%Y-%m-%d'), 'expiry_time': later.strftime('%H:%M'), 'reason': 'x'})
            target = later
        self.assertEqual(self._adjustments().count(), 9)

    def test_admin_cannot_set_expiry_in_the_past_or_with_missing_fields(self):
        self.client.force_login(self.admin)
        past = timezone.localtime(self.sub.expiry_time) - dt.timedelta(days=1)
        self.client.post(self.add_url, {'expiry_date': past.strftime('%Y-%m-%d'),
                                        'expiry_time': past.strftime('%H:%M'), 'reason': 'x'})
        self.client.post(self.add_url, {'expiry_date': '', 'expiry_time': '', 'reason': 'x'})
        self.assertEqual(self._adjustments().count(), 0)

    # ---- pages --------------------------------------------------------------
    def test_subscriptions_page_has_right_controls_per_role(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('core:subscriptions')).content.decode()
        self.assertIn('name="expiry_date"', html)
        self.assertNotIn('name="minutes"', html)
        self.assertNotIn('Request more adjustments', html)
        self.client.force_login(self.sh)
        html = self.client.get(reverse('core:subscriptions')).content.decode()
        self.assertIn('name="minutes"', html)
        self.assertNotIn('name="expiry_date"', html)
        self.assertIn('btn-spinner', html)

    def test_request_page_is_main_admin_only(self):
        self.client.force_login(self.sh)
        self.assertEqual(self.client.get(reverse('core:time_adjustment_requests_admin')).status_code, 403)

    def test_dashboard_charts_use_responsive_grid_class(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('grid chart-grid', html)
        self.assertNotIn('grid-template-columns: 1fr 1fr', html)
