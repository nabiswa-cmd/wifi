"""Connect / disconnect from the admin portal, and the "paid, not connected" badge."""
import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing.models import Payment, Subscription
from apps.core import connections as conn
from apps.core.models import AuditLog, SystemSettings
from apps.customers.models import Customer
from apps.mikrotik.models import InternetSession, MikroTikJob, MikroTikProfile, MikroTikRouter
from apps.packages.models import InternetPackage

MAC = 'AA:BB:CC:DD:EE:01'


def _staff(username, role):
    user = User.objects.create_user(username=username, password='pw12345!', is_staff_account=True)
    r, _ = Role.objects.get_or_create(name=role)
    StaffProfile.objects.create(user=user, role=r)
    return user


class Base(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        self.admin = _staff('boss', 'SUPER_ADMIN')
        self.sh = _staff('sh', 'SHAREHOLDER')
        self.router = MikroTikRouter.objects.create(name='R', host='1.1.1.1', username='u', password='p')
        profile = MikroTikProfile.objects.create(router=self.router, profile_name='DAY')
        self.pkg = InternetPackage.objects.create(
            name='Day', price=100, duration=1, duration_unit='DAYS', mikrotik_profile=profile)
        self.now = timezone.now()

    def paid(self, name, phone, minutes_ago=10, hours_left=5, mac=MAC, online=False, receipt='ABC123'):
        cust = Customer.objects.create(full_name=name, phone_number=phone)
        pay = Payment.objects.create(customer=cust, package=self.pkg, phone_number=phone, amount=100,
                                     status=Payment.Status.SUCCESS, mpesa_receipt_number=receipt, mac_address=mac)
        when = self.now - dt.timedelta(minutes=minutes_ago)
        sub = Subscription.objects.create(
            customer=cust, package=self.pkg, payment=pay, activation_time=when,
            expiry_time=self.now + dt.timedelta(hours=hours_left), status=Subscription.Status.ACTIVE,
            mikrotik_username=mac)
        if online:
            InternetSession.objects.create(customer=cust, subscription=sub, payment=pay, router=self.router,
                                           mac_address=mac, status='ACTIVE', login_time=self.now)
        return sub


class BadgeAndFilterTests(Base):
    def test_unconnected_filter_and_badge(self):
        a = self.paid('Not Connected Nina', '254700000001', receipt='A1', mac='AA:BB:CC:DD:EE:01')
        self.paid('Online Omari', '254700000002', online=True, receipt='A2', mac='AA:BB:CC:DD:EE:02')
        self.paid('Just Paid Juma', '254700000003', minutes_ago=0, receipt='A3', mac='AA:BB:CC:DD:EE:03')
        self.paid('Expired Esther', '254700000004', hours_left=-1, receipt='A4', mac='AA:BB:CC:DD:EE:04')
        self.assertEqual([s.pk for s in conn.subscriptions('unconnected')], [a.pk])
        self.assertEqual(conn.count('online'), 1)
        self.client.force_login(self.admin)
        html = self.client.get(reverse('core:connections')).content.decode()
        self.assertIn('Not Connected Nina', html)
        self.assertIn('Paid &middot; not connected', html)
        self.assertNotIn('Online Omari', html)

    def test_dashboard_tile_matches_list(self):
        self.paid('Nina', '254700000001')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('core:dashboard'))
        self.assertEqual(resp.context['unconnected_paid'], 1)
        self.assertContains(resp, 'filter=unconnected')
        self.assertContains(resp, 'bi-wifi-off')

    def test_shareholder_sees_list_but_no_buttons(self):
        sub = self.paid('Nina', '254700000001')
        self.client.force_login(self.sh)
        resp = self.client.get(reverse('core:connections'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Disconnect</button>')
        self.assertEqual(self.client.post(reverse('core:connection_connect', args=[sub.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('core:connection_disconnect', args=[sub.pk])).status_code, 403)

    def test_non_staff_forbidden(self):
        self.client.force_login(User.objects.create_user(username='cust', password='pw12345!'))
        self.assertEqual(self.client.get(reverse('core:connections')).status_code, 403)


class ConnectTests(Base):
    def test_connect_queues_bypass_for_saved_mac_and_audits(self):
        sub = self.paid('Nina', '254700000001')
        self.client.force_login(self.admin)
        self.client.post(reverse('core:connection_connect', args=[sub.pk]))
        job = MikroTikJob.objects.filter(job_type='BYPASS_MAC').first()
        self.assertIsNotNone(job)
        self.assertTrue(InternetSession.objects.filter(subscription=sub, mac_address=MAC, status='ACTIVE').exists())
        self.assertTrue(AuditLog.objects.filter(action=conn.CONNECT_ACTION, object_id=str(sub.pk)).exists())
        # staff IP must not be stored as the device IP
        self.assertNotEqual(InternetSession.objects.get(subscription=sub).ip_address, '127.0.0.1')

    def test_connect_without_mac_asks_for_one(self):
        sub = self.paid('Nina', '254700000001', mac='')
        ok, msg = conn.connect(sub.pk, self.admin)
        self.assertFalse(ok)
        self.assertIn('MAC', msg)
        ok, _ = conn.connect(sub.pk, self.admin, mac='aa-bb-cc-dd-ee-09')
        self.assertTrue(ok)
        self.assertTrue(MikroTikJob.objects.filter(job_type='BYPASS_MAC').exists())

    def test_cannot_connect_expired(self):
        sub = self.paid('Esther', '254700000004', hours_left=-1)
        ok, _ = conn.connect(sub.pk, self.admin)
        self.assertFalse(ok)
        self.assertFalse(MikroTikJob.objects.exists())


class DisconnectTests(Base):
    def test_disconnect_pauses_and_cuts_off(self):
        sub = self.paid('Nina', '254700000001', online=True)
        self.client.force_login(self.admin)
        self.client.post(reverse('core:connection_disconnect', args=[sub.pk]), {'reason': 'abuse'})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.SUSPENDED)
        self.assertFalse(sub.is_currently_entitled())          # so customer cannot self-reconnect
        types = set(MikroTikJob.objects.values_list('job_type', flat=True))
        self.assertIn('UNBYPASS_MAC', types)
        self.assertIn('DISABLE_USER', types)
        self.assertFalse(InternetSession.objects.filter(subscription=sub, status='ACTIVE').exists())
        log = AuditLog.objects.get(action=conn.PAUSE_ACTION)
        self.assertEqual(log.new_value['reason'], 'abuse')

    def test_customer_self_reconnect_is_blocked_while_paused(self):
        sub = self.paid('Nina', '254700000001', receipt='SFH3JT6LKQ')
        conn.disconnect(sub.pk, self.admin)
        resp = self.client.post(reverse('billing:reconnect'), {'code': 'SFH3JT6LKQ', 'mac': MAC})
        self.assertFalse(MikroTikJob.objects.filter(job_type='BYPASS_MAC').exists())

    def test_reconnect_gives_back_paused_time(self):
        sub = self.paid('Nina', '254700000001', hours_left=5)
        original = sub.expiry_time
        conn.disconnect(sub.pk, self.admin)
        log = AuditLog.objects.get(action=conn.PAUSE_ACTION)
        paused_at = self.now - dt.timedelta(minutes=30)
        log.new_value['paused_at'] = paused_at.isoformat()
        log.save()
        ok, _ = conn.connect(sub.pk, self.admin)
        self.assertTrue(ok)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        gained = (sub.expiry_time - original).total_seconds()
        self.assertAlmostEqual(gained, 30 * 60, delta=30)

    def test_only_active_can_be_disconnected(self):
        sub = self.paid('Esther', '254700000004')
        Subscription.objects.filter(pk=sub.pk).update(status='EXPIRED')
        ok, _ = conn.disconnect(sub.pk, self.admin)
        self.assertFalse(ok)
