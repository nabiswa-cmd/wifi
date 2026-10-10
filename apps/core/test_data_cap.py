import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing.models import Subscription
from apps.core import data_cap
from apps.core.models import SystemSettings
from apps.customers.models import Customer
from apps.mikrotik.models import InternetSession
from apps.packages.models import InternetPackage

GB = 1024 ** 3


def _aware(y, m, d, h=12):
    return timezone.make_aware(dt.datetime(y, m, d, h))


class CycleTests(TestCase):
    def test_cycle_bounds(self):
        s, e = data_cap.cycle_bounds(_aware(2026, 10, 10), start_day=1)
        self.assertEqual((timezone.localtime(s).date(), timezone.localtime(e).date()), (dt.date(2026, 10, 1), dt.date(2026, 11, 1)))
        s, e = data_cap.cycle_bounds(_aware(2026, 10, 10), start_day=15)      # before the 15th: cycle began last month
        self.assertEqual((timezone.localtime(s).date(), timezone.localtime(e).date()), (dt.date(2026, 9, 15), dt.date(2026, 10, 15)))
        s, e = data_cap.cycle_bounds(_aware(2026, 12, 20), start_day=15)      # year rollover
        self.assertEqual(timezone.localtime(e).date(), dt.date(2027, 1, 15))
        s, e = data_cap.cycle_bounds(_aware(2026, 3, 5), start_day=31)        # short month clamps
        self.assertEqual(timezone.localtime(s).date(), dt.date(2026, 2, 28))

    def test_human(self):
        self.assertEqual(data_cap.human(512), '512 B')
        self.assertEqual(data_cap.human(5 * 1024 ** 2), '5.0 MB')
        self.assertEqual(data_cap.human(1.5 * 1024 ** 4), '1.50 TB')


class SummaryTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        pkg = InternetPackage.objects.create(name='Day', price=100, duration=1, duration_unit='DAYS')
        cust = Customer.objects.create(full_name='A', phone_number='254700000001')
        self.sub = Subscription.objects.create(customer=cust, package=pkg, status='ACTIVE',
                                               activation_time=timezone.now(), expiry_time=timezone.now() + dt.timedelta(days=1))
        self.cust = cust

    def session(self, down_gb, up_gb, used_at):
        return InternetSession.objects.create(
            customer=self.cust, subscription=self.sub, mac_address='AA:BB:CC:DD:EE:01',
            bytes_downloaded=int(down_gb * GB), bytes_uploaded=int(up_gb * GB), last_usage_at=used_at)

    def test_only_this_cycle_counts_toward_cap(self):
        now = timezone.now()
        start, _ = data_cap.cycle_bounds(now)
        self.session(300, 20, now)
        self.session(500, 0, start - dt.timedelta(days=3))     # last cycle
        out = data_cap.summary(now)
        self.assertEqual(out['used_h'], '320.0 GB')
        self.assertEqual(out['all_time_h'], '820.0 GB')
        self.assertAlmostEqual(out['percent'], 320 / (data_cap.CAP_GB) * 100, places=1)
        self.assertFalse(out['over'])

    def test_over_the_cap(self):
        now = timezone.now()
        self.session(data_cap.CAP_GB + 10, 0, now)
        out = data_cap.summary(now)
        self.assertTrue(out['over'])
        self.assertEqual(out['bar_percent'], 100)

    def test_page_shows_the_card(self):
        user = User.objects.create_user(username='boss', password='pw12345!', is_staff_account=True)
        role, _ = Role.objects.get_or_create(name='SUPER_ADMIN')
        StaffProfile.objects.create(user=user, role=role)
        self.session(10, 1, timezone.now())
        self.client.force_login(user)
        resp = self.client.get(reverse('core:fup_monitor'))
        self.assertContains(resp, 'Plan data used')
        self.assertContains(resp, '11.0 GB')
