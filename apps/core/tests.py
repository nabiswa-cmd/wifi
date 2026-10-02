"""
Tests for the Company-controlled billing cycle (Subscribe / Pay all
shareholders) and for the voucher connection-timing fix.
"""
import datetime as dt
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing.models import (
    EarningsPeriod, Payment, Shareholder, ShareholderPayout, Subscription, WithdrawalRequest,
)
from apps.core.models import SystemSettings
from apps.customers.models import Customer
from apps.packages.models import InternetPackage


def _make_user(username, role_name, email=''):
    user = User.objects.create_user(username=username, password='pw12345!', email=email, is_staff_account=True)
    role, _ = Role.objects.get_or_create(name=role_name)
    StaffProfile.objects.create(user=user, role=role)
    return user


class BillingCycleTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={
            'total_capital': Decimal('1000'), 'subscription_cost': Decimal('500'),
        })
        self.admin = _make_user('boss', 'SUPER_ADMIN', 'boss@example.com')
        self.sh_user = _make_user('sh1', 'SHAREHOLDER', 'sh1@example.com')
        self.noemail_user = _make_user('sh2', 'SHAREHOLDER', '')
        self.sh1 = Shareholder.objects.create(user=self.sh_user, full_name='Sharon', contribution=Decimal('600'))
        self.sh2 = Shareholder.objects.create(user=self.noemail_user, full_name='Nomail', contribution=Decimal('400'))

        pkg = InternetPackage.objects.create(name='Day', price=100, duration=1, duration_unit='DAYS')
        cust = Customer.objects.create(full_name='c', phone_number='254712345678')
        self.start = timezone.now() - dt.timedelta(days=20)
        EarningsPeriod.objects.create(label='September earnings', start_at=self.start)
        # 2000 of revenue inside the cycle, 777 BEFORE it (must not count).
        for amt, when in [(1200, 10), (800, 5), (777, 25)]:
            p = Payment.objects.create(customer=cust, package=pkg, phone_number='254712345678',
                                       amount=amt, status=Payment.Status.SUCCESS)
            Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - dt.timedelta(days=when))
        Payment.objects.create(customer=cust, package=pkg, phone_number='254712345678',
                               amount=9999, status=Payment.Status.FAILED)

    def _login(self, user):
        self.client.force_login(user)

    def test_dashboard_counts_only_current_cycle(self):
        self._login(self.admin)
        resp = self.client.get(reverse('core:revenue_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.context['company_revenue']), Decimal('2000'))
        self.assertEqual(resp.context['distributable_profit'], Decimal('1500'))
        self.assertContains(resp, 'September earnings')
        self.assertContains(resp, 'Subscribe')

    def test_shareholder_never_sees_admin_buttons(self):
        self._login(self.sh_user)
        resp = self.client.get(reverse('core:revenue_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Subscribe')
        self.assertNotContains(resp, 'Pay all shareholders')

    def test_shareholder_cannot_post_subscribe_or_pay(self):
        self._login(self.sh_user)
        self.assertEqual(self.client.post(reverse('core:subscribe_new_period')).status_code, 403)
        self.assertEqual(self.client.post(reverse('core:pay_all_shareholders'), {'period_id': 1}).status_code, 403)
        self.assertTrue(EarningsPeriod.objects.get().is_open)

    def test_subscribe_closes_period_and_freezes_profit(self):
        self._login(self.admin)
        self.client.post(reverse('core:subscribe_new_period'))
        old = EarningsPeriod.objects.get(label='September earnings')
        self.assertFalse(old.is_open)
        self.assertEqual(old.revenue, Decimal('2000.00'))
        self.assertEqual(old.distributable_profit, Decimal('1500.00'))
        new = EarningsPeriod.current()
        self.assertTrue(new.is_open)
        self.assertEqual(new.start_at, old.end_at)           # contiguous, no gap
        self.assertEqual(new.day_number, 1)
        self.assertEqual(new.compute_revenue(), Decimal('0.00'))  # fresh cycle starts at 0
        pays = {p.shareholder_id: p for p in old.payouts.all()}
        self.assertEqual(pays[self.sh1.id].earnings, Decimal('900.00'))  # 60% of 1500
        self.assertEqual(pays[self.sh2.id].earnings, Decimal('600.00'))

    def test_custom_start_in_past_moves_revenue_to_new_cycle(self):
        self._login(self.admin)
        day_one = (timezone.localtime() - dt.timedelta(days=7)).replace(second=0, microsecond=0, tzinfo=None)
        self.client.post(reverse('core:subscribe_new_period'),
                         {'start_at': day_one.strftime('%Y-%m-%dT%H:%M'), 'label': 'My cycle'})
        old = EarningsPeriod.objects.get(label='September earnings')
        self.assertEqual(old.revenue, Decimal('1200.00'))     # the 5-day-old 800 now belongs to the new cycle
        cur = EarningsPeriod.current()
        self.assertEqual(cur.label, 'My cycle')
        self.assertEqual(cur.compute_revenue(), Decimal('800.00'))

    def test_future_start_rejected(self):
        self._login(self.admin)
        future = (timezone.localtime() + dt.timedelta(days=3)).replace(tzinfo=None)
        self.client.post(reverse('core:subscribe_new_period'), {'start_at': future.strftime('%Y-%m-%dT%H:%M')})
        self.assertTrue(EarningsPeriod.objects.get().is_open)

    def test_pay_all_pays_everyone_emails_and_is_idempotent(self):
        self._login(self.admin)
        self.client.post(reverse('core:subscribe_new_period'))
        old = EarningsPeriod.objects.get(label='September earnings')
        mail.outbox.clear()

        self.client.post(reverse('core:pay_all_shareholders'), {'period_id': old.id})
        payouts = {p.shareholder_id: p for p in ShareholderPayout.objects.filter(period=old)}
        self.assertTrue(all(p.status == 'PAID' for p in payouts.values()))
        self.assertEqual(payouts[self.sh1.id].amount_paid, Decimal('900.00'))
        old.refresh_from_db()
        self.assertIsNotNone(old.paid_out_at)

        # Only the shareholder with an email on file gets one, and it states the amount.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sh1@example.com'])
        self.assertIn('900.00', mail.outbox[0].body)
        self.assertIn('September earnings', mail.outbox[0].subject)

        # Second click: nothing paid again, no more emails.
        self.client.post(reverse('core:pay_all_shareholders'), {'period_id': old.id})
        self.assertEqual(len(mail.outbox), 1)

    def test_pay_all_deducts_prior_withdrawals(self):
        self._login(self.admin)
        WithdrawalRequest.objects.create(
            shareholder=self.sh1, amount=Decimal('300'), period_start=EarningsPeriod.current().start_date,
            payment_phone='0712', status=WithdrawalRequest.Status.PAID,
        )
        self.client.post(reverse('core:subscribe_new_period'))
        old = EarningsPeriod.objects.get(label='September earnings')
        self.client.post(reverse('core:pay_all_shareholders'), {'period_id': old.id})
        p = ShareholderPayout.objects.get(period=old, shareholder=self.sh1)
        self.assertEqual(p.earnings, Decimal('900.00'))
        self.assertEqual(p.amount_paid, Decimal('600.00'))    # 900 earned - 300 already withdrawn

    def test_cannot_pay_the_open_period(self):
        self._login(self.admin)
        cur = EarningsPeriod.current()
        resp = self.client.post(reverse('core:pay_all_shareholders'), {'period_id': cur.id})
        self.assertEqual(resp.status_code, 404)

    def test_withdrawal_limit_uses_current_cycle(self):
        self._login(self.sh_user)
        self.client.post(reverse('core:request_withdrawal'), {'amount': '900', 'payment_phone': '0712'})
        self.assertEqual(WithdrawalRequest.objects.count(), 1)
        self.assertEqual(WithdrawalRequest.objects.get().period_start, EarningsPeriod.current().start_date)
        self.client.post(reverse('core:request_withdrawal'), {'amount': '1', 'payment_phone': '0712'})
        self.assertEqual(WithdrawalRequest.objects.count(), 1)  # 900 is the whole 60% share; nothing left


class VoucherConnectTimingTests(TestCase):
    """The router job must be created AFTER the session login_time, or voucher_status never sees it."""

    def test_bypass_job_is_not_older_than_session_login_time(self):
        from django.test import RequestFactory
        from apps.mikrotik.models import InternetSession, MikroTikJob, MikroTikProfile, MikroTikRouter
        from apps.mikrotik.services import connect_voucher_device
        from apps.vouchers.models import Voucher
        from apps.vouchers import views as vviews

        router = MikroTikRouter.objects.create(name='r', host='1.1.1.1', username='u', password='p', is_active=True)
        prof = MikroTikProfile.objects.create(router=router, profile_name='p1')
        pkg = InternetPackage.objects.create(name='V', price=50, duration=1, duration_unit='DAYS', mikrotik_profile=prof)
        cust = Customer.objects.create(full_name='c', phone_number='254700000001')
        voucher = Voucher.objects.create(code='ABC123', package=pkg)
        sub = Subscription.activate_from_voucher(cust, pkg, voucher)
        voucher.status, voucher.customer = Voucher.Status.USED, cust
        voucher.save()

        mac = 'AA:BB:CC:DD:EE:FF'
        req = RequestFactory().post('/vouchers/redeem/', {'mac': mac})
        self.assertIsNone(connect_voucher_device(req, voucher))

        session = InternetSession.objects.get(voucher=voucher, mac_address=mac)
        bypass = MikroTikJob.objects.get(job_type='BYPASS_MAC')
        self.assertGreaterEqual(bypass.created_at, session.login_time)

        # Not connected until the agent reports DONE, then connected immediately.
        get = RequestFactory().get(f'/vouchers/{voucher.id}/status/', {'mac': mac})
        import json
        self.assertFalse(json.loads(vviews.voucher_status(get, voucher.id).content)['connected'])
        MikroTikJob.objects.filter(pk=bypass.pk).update(status='DONE')
        self.assertTrue(json.loads(vviews.voucher_status(get, voucher.id).content)['connected'])
        # And the status poll never queued duplicate work while the job was in flight.
        self.assertEqual(MikroTikJob.objects.filter(job_type='BYPASS_MAC').count(), 1)

    def test_status_requeues_when_first_bypass_failed(self):
        import json
        from django.test import RequestFactory
        from apps.mikrotik.models import MikroTikJob, MikroTikProfile, MikroTikRouter
        from apps.mikrotik.services import connect_voucher_device
        from apps.vouchers.models import Voucher
        from apps.vouchers import views as vviews

        router = MikroTikRouter.objects.create(name='r', host='1.1.1.1', username='u', password='p', is_active=True)
        prof = MikroTikProfile.objects.create(router=router, profile_name='p1')
        pkg = InternetPackage.objects.create(name='V', price=50, duration=1, duration_unit='DAYS', mikrotik_profile=prof)
        cust = Customer.objects.create(full_name='c', phone_number='254700000002')
        voucher = Voucher.objects.create(code='XYZ999', package=pkg)
        Subscription.activate_from_voucher(cust, pkg, voucher)
        voucher.status, voucher.customer = Voucher.Status.USED, cust
        voucher.save()
        mac = 'AA:BB:CC:DD:EE:01'
        connect_voucher_device(RequestFactory().post('/x/', {'mac': mac}), voucher)
        MikroTikJob.objects.filter(job_type='BYPASS_MAC').update(status='FAILED')

        get = RequestFactory().get('/s/', {'mac': mac})
        vviews.voucher_status(get, voucher.id)
        self.assertEqual(MikroTikJob.objects.filter(job_type='BYPASS_MAC', status='PENDING').count(), 1)
