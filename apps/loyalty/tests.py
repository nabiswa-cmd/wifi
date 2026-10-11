import datetime as dt
import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, StaffProfile, User
from apps.billing.models import Payment, Subscription
from apps.core.models import SystemSettings
from apps.customers.models import Customer
from apps.loyalty import services
from apps.loyalty.models import LoyaltyConfig, LoyaltyReward, PointsLedger
from apps.packages.models import InternetPackage
from apps.vouchers.models import Voucher

PHONE = '254712345678'


class Base(TestCase):
    def setUp(self):
        cache.clear()
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        self.config = LoyaltyConfig.load()
        self.config.enabled = True
        self.config.save()
        self.day = InternetPackage.objects.create(name='Day', price=Decimal('100'), duration=1, duration_unit='DAYS')
        self.hour = InternetPackage.objects.create(name='1 Hour', price=Decimal('10'), duration=1, duration_unit='HOURS')
        # 1 point per KSh 10, so KSh 100 earns 10 points; a KSh 10 voucher for 100 points is a 1% rebate... use 20.
        self.reward = LoyaltyReward.objects.create(package=self.hour, points_cost=20)  # 10 / 200 = 5%
        self.cust = Customer.objects.create(full_name='Wanjiku', phone_number=PHONE)

    def pay(self, amount=100, receipt='SFH3JT6LKQ', status=Payment.Status.SUCCESS, package=None, customer=None):
        return Payment.objects.create(
            customer=customer or self.cust, package=package or self.day, phone_number=PHONE, amount=Decimal(amount),
            status=status, mpesa_receipt_number=receipt,
        )

    def give(self, points):
        PointsLedger.objects.create(customer=self.cust, kind='ADJUST', points=points, note='test')


class EarningTests(Base):
    def test_whole_points_floor(self):
        self.assertEqual(services.points_for_amount(Decimal('95'), self.config), 9)
        self.assertEqual(services.points_for_amount(Decimal('9.99'), self.config), 0)
        self.assertEqual(services.points_for_amount(Decimal('100'), self.config), 10)

    def test_success_payment_earns_points(self):
        entry = services.award_for_payment(self.pay(100))
        self.assertEqual(entry.points, 10)
        self.assertEqual(services.balance(self.cust), 10)

    def test_repeat_award_for_same_payment_adds_nothing(self):
        p = self.pay(100)
        services.award_for_payment(p)
        self.assertIsNone(services.award_for_payment(p))
        self.assertIsNone(services.award_for_payment(p))
        self.assertEqual(PointsLedger.objects.filter(payment=p).count(), 1)

    def test_nothing_earned_when_programme_off(self):
        self.config.enabled = False
        self.config.save()
        self.assertIsNone(services.award_for_payment(self.pay(100)))
        self.assertEqual(PointsLedger.objects.count(), 0)

    def test_nothing_earned_for_unconfirmed_payments(self):
        for st in (Payment.Status.PENDING, Payment.Status.FAILED, Payment.Status.CANCELLED, Payment.Status.TIMEOUT):
            self.assertIsNone(services.award_for_payment(self.pay(100, receipt=None, status=st)))
        self.assertEqual(PointsLedger.objects.count(), 0)

    def test_payment_too_small_for_a_point_earns_nothing(self):
        self.assertIsNone(services.award_for_payment(self.pay(5)))

    def test_refund_takes_the_points_back_once(self):
        p = self.pay(100)
        services.award_for_payment(p)
        p.status = Payment.Status.REFUNDED
        p.save()          # signal reverses
        p.save()          # saving again must not reverse twice
        self.assertEqual(services.balance(self.cust), 0)
        self.assertEqual(PointsLedger.objects.filter(payment=p, kind='REVERSAL').count(), 1)

    def test_refund_after_spending_leaves_a_negative_balance_not_free_points(self):
        p = self.pay(200)
        services.award_for_payment(p)                # 20 points
        services.redeem(self.cust, self.reward.id)   # spends 20
        p.status = Payment.Status.REFUNDED
        p.save()
        self.assertEqual(services.balance(self.cust), -20)
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)


class CallbackIntegrationTests(Base):
    def callback(self, checkout='ws_CO_1', result=0, receipt='SFH3JT6LKQ'):
        body = {'Body': {'stkCallback': {
            'CheckoutRequestID': checkout, 'ResultCode': result,
            'CallbackMetadata': {'Item': [{'Name': 'MpesaReceiptNumber', 'Value': receipt},
                                          {'Name': 'TransactionDate', 'Value': 20261010120000}]},
        }}}
        return self.client.post('/api/mpesa/callback/', data=json.dumps(body), content_type='application/json')

    def pending(self, amount=100):
        return Payment.objects.create(customer=self.cust, package=self.day, phone_number=PHONE, amount=Decimal(amount),
                                      checkout_request_id='ws_CO_1', status=Payment.Status.PENDING)

    def test_real_callback_earns_points_once_even_if_safaricom_repeats_it(self):
        self.pending(100)
        self.callback(); self.callback(); self.callback()
        self.assertEqual(services.balance(self.cust), 10)
        self.assertEqual(Subscription.objects.filter(customer=self.cust).count(), 1)

    def test_failed_payment_earns_nothing(self):
        self.pending(100)
        self.callback(result=1)
        self.assertEqual(PointsLedger.objects.count(), 0)

    def test_a_loyalty_bug_can_never_break_a_real_payment(self):
        p = self.pending(100)
        with mock.patch('apps.loyalty.services.award_for_payment', side_effect=RuntimeError('boom')):
            resp = self.callback()
        self.assertEqual(resp.status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.SUCCESS)
        self.assertTrue(Subscription.objects.filter(payment=p, status='ACTIVE').exists())


class RewardSafetyTests(Base):
    def test_rebate_math(self):
        # KSh 10 package, 20 points at KSh 10/point = KSh 200 spend -> 5%
        self.assertEqual(self.reward.rebate_percent(self.config), Decimal('5.00'))

    def test_reward_above_the_ceiling_cannot_be_saved(self):
        bad = LoyaltyReward(package=self.day, points_cost=5)   # KSh 100 for KSh 50 spend = 200%
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_reward_at_or_under_the_ceiling_is_fine(self):
        ok = LoyaltyReward(package=self.day, points_cost=100)  # 100 / 1000 = 10%, exactly the ceiling
        ok.full_clean()

    def test_raising_a_package_price_later_switches_the_reward_off(self):
        self.hour.price = Decimal('40')                          # 40 / 200 = 20% now
        self.hour.save()
        self.reward.refresh_from_db()
        self.assertFalse(self.reward.is_offerable(self.config))
        self.give(50)
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)

    def test_disabled_package_or_reward_is_not_offered(self):
        self.hour.is_active = False
        self.hour.save()
        self.assertEqual(services.offerable_rewards(self.config), [])


class RedeemTests(Base):
    def test_redeem_spends_points_and_issues_a_voucher_for_this_customer_only(self):
        self.give(30)
        v = services.redeem(self.cust, self.reward.id)
        self.assertEqual(services.balance(self.cust), 10)
        self.assertEqual((v.customer, v.package, v.status), (self.cust, self.hour, Voucher.Status.UNUSED))
        self.assertGreater(v.expiry_date, timezone.now() + dt.timedelta(days=6))
        self.assertEqual(PointsLedger.objects.get(voucher=v).points, -20)

    def test_not_enough_points(self):
        self.give(19)
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)
        self.assertEqual(Voucher.objects.count(), 0)
        self.assertEqual(services.balance(self.cust), 19)

    def test_one_unused_reward_voucher_at_a_time(self):
        self.give(100)
        first = services.redeem(self.cust, self.reward.id)
        with self.assertRaises(services.LoyaltyError) as ctx:
            services.redeem(self.cust, self.reward.id)
        self.assertIn(first.code, str(ctx.exception))
        self.assertEqual(Voucher.objects.count(), 1)
        self.assertEqual(services.balance(self.cust), 80)

    def test_can_redeem_again_once_the_voucher_is_used(self):
        self.give(100)
        v = services.redeem(self.cust, self.reward.id)
        v.status = Voucher.Status.USED
        v.save()
        services.redeem(self.cust, self.reward.id)
        self.assertEqual(services.balance(self.cust), 60)

    def test_monthly_cap(self):
        self.config.max_redemptions_per_30_days = 2
        self.config.save()
        self.give(500)
        for _ in range(2):
            v = services.redeem(self.cust, self.reward.id)
            v.status = Voucher.Status.USED
            v.save()
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)

    def test_off_switch(self):
        self.give(100)
        self.config.enabled = False
        self.config.save()
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)

    def test_suspended_customer_cannot_redeem(self):
        self.give(100)
        self.cust.status = Customer.Status.SUSPENDED
        self.cust.save()
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)

    def test_unknown_reward(self):
        self.give(100)
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, 99999)

    def test_balance_never_goes_below_what_was_held(self):
        self.give(20)
        services.redeem(self.cust, self.reward.id)
        self.assertEqual(services.balance(self.cust), 0)


class ExpiryTests(Base):
    def earn_at(self, days_ago, points=10):
        e = PointsLedger.objects.create(customer=self.cust, kind='EARN', points=points, note='old')
        PointsLedger.objects.filter(pk=e.pk).update(created_at=timezone.now() - dt.timedelta(days=days_ago))

    def test_points_lapse_after_no_earning_for_the_period(self):
        self.earn_at(200)
        self.assertEqual(services.expire_if_stale(self.cust), 10)
        self.assertEqual(services.balance(self.cust), 0)

    def test_recent_earning_keeps_everything(self):
        self.earn_at(200)
        self.earn_at(5)
        self.assertEqual(services.expire_if_stale(self.cust), 0)
        self.assertEqual(services.balance(self.cust), 20)

    def test_zero_means_never_expire(self):
        self.config.expiry_days = 0
        self.config.save()
        self.earn_at(2000)
        self.assertEqual(services.expire_if_stale(self.cust), 0)
        self.assertEqual(services.balance(self.cust), 10)

    def test_expired_points_cannot_be_redeemed(self):
        self.earn_at(200, points=50)
        with self.assertRaises(services.LoyaltyError):
            services.redeem(self.cust, self.reward.id)


class VerificationTests(Base):
    def test_right_phone_and_code(self):
        self.pay(100, receipt='SFH3JT6LKQ')
        self.assertEqual(services.verify_ownership('0712345678', 'sfh3jt6lkq'), self.cust)

    def test_whole_sms_can_be_pasted(self):
        self.pay(100, receipt='SFH3JT6LKQ')
        self.assertEqual(services.verify_ownership('0712345678', 'SFH3JT6LKQ Confirmed. Ksh100.00 paid'), self.cust)

    def test_someone_elses_receipt_does_not_verify_you(self):
        other = Customer.objects.create(full_name='Other', phone_number='254798765432')
        self.pay(100, receipt='AAA1111111', customer=other)
        self.assertIsNone(services.verify_ownership('0712345678', 'AAA1111111'))

    def test_failed_payment_code_does_not_verify(self):
        self.pay(100, receipt='FAIL000001', status=Payment.Status.FAILED)
        self.assertIsNone(services.verify_ownership('0712345678', 'FAIL000001'))

    def test_garbage(self):
        self.assertIsNone(services.verify_ownership('', ''))
        self.assertIsNone(services.verify_ownership('0712345678', 'nope'))
        self.assertIsNone(services.verify_ownership('0799999999', 'SFH3JT6LKQ'))


class PageFlowTests(Base):
    def lookup(self, phone='0712345678'):
        return self.client.post(reverse('loyalty:lookup'), {'phone': phone})

    def verify(self, code='SFH3JT6LKQ'):
        return self.client.post(reverse('loyalty:verify'), {'code': code})

    def test_page_loads_and_asks_for_a_number(self):
        resp = self.client.get(reverse('loyalty:home'))
        self.assertContains(resp, 'Show my points')

    def test_off_switch_shows_coming_soon(self):
        self.config.enabled = False
        self.config.save()
        self.assertContains(self.client.get(reverse('loyalty:home')), 'coming soon')

    def test_lookup_shows_balance_but_no_history_and_no_redeeming(self):
        self.pay(100)
        services.award_for_payment(Payment.objects.get())
        self.lookup()
        resp = self.client.get(reverse('loyalty:home'))
        self.assertEqual(resp.context['snap']['points'], 10)
        self.assertFalse(resp.context['verified'])
        self.assertEqual(list(resp.context['history']), [])
        r = self.client.post(reverse('loyalty:redeem'), {'reward_id': self.reward.id})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Voucher.objects.count(), 0)

    def test_unknown_number_looks_like_zero_points(self):
        self.lookup('0711000000')
        resp = self.client.get(reverse('loyalty:home'))
        self.assertEqual(resp.context['snap']['points'], 0)
        self.assertFalse(Customer.objects.filter(phone_number='254711000000').exists())  # lookups create nothing

    def test_bad_number(self):
        self.assertContains(self.lookup('123'), 'valid Safaricom number')

    def test_verify_with_wrong_code_fails_and_unlocks_nothing(self):
        self.pay(100)
        self.lookup()
        self.assertEqual(self.verify('WRONGCODE1').status_code, 400)
        self.give(100)
        self.assertEqual(self.client.post(reverse('loyalty:redeem'), {'reward_id': self.reward.id}).status_code, 403)

    def test_full_journey_earn_verify_redeem_then_connect_with_existing_voucher_flow(self):
        p = self.pay(300)
        services.award_for_payment(p)                         # 30 points
        self.lookup()
        self.assertTrue(self.verify().json()['success'])
        data = self.client.post(reverse('loyalty:redeem'), {'reward_id': self.reward.id}).json()
        self.assertTrue(data['success'])
        self.assertEqual(data['points'], 10)
        self.assertEqual(data['phone'], '0712345678')
        # The voucher works through the portal's normal voucher endpoint, for this number only.
        ok = self.client.post('/vouchers/redeem/', {'voucher_code': data['code'], 'voucher_phone': '0712345678'}).json()
        self.assertTrue(ok['success'], ok)
        self.assertTrue(Subscription.objects.filter(customer=self.cust, voucher__code=data['code']).exists())
        other = self.client.post('/vouchers/redeem/', {'voucher_code': data['code'], 'voucher_phone': '0798765432'}).json()
        self.assertFalse(other['success'])

    def test_redeeming_a_reward_earns_no_new_points(self):
        self.give(50)
        v = services.redeem(self.cust, self.reward.id)
        self.client.post('/vouchers/redeem/', {'voucher_code': v.code, 'voucher_phone': '0712345678'})
        self.assertFalse(PointsLedger.objects.filter(kind='EARN').exists())
        self.assertEqual(services.balance(self.cust), 30)

    def test_live_endpoint_follows_the_balance(self):
        self.lookup()
        self.assertEqual(self.client.get(reverse('loyalty:live')).json()['points'], 0)
        self.give(7)
        data = self.client.get(reverse('loyalty:live')).json()
        self.assertEqual(data['points'], 7)
        self.assertEqual(data['next_reward']['short_by'], 13)
        self.assertEqual(data['rewards'][0]['affordable'], False)
        self.give(20)
        self.assertTrue(self.client.get(reverse('loyalty:live')).json()['rewards'][0]['affordable'])

    def test_live_endpoint_without_a_session_leaks_nothing(self):
        self.give(99)
        data = self.client.get(reverse('loyalty:live')).json()
        self.assertEqual(data['points'], 0)

    def test_lookup_is_rate_limited(self):
        codes = [self.lookup().status_code for _ in range(22)]
        self.assertEqual(codes[-1], 429)

    def test_forget_clears_the_session(self):
        self.lookup()
        self.client.post(reverse('loyalty:forget'))
        self.assertContains(self.client.get(reverse('loyalty:home')), 'Show my points')

    def test_landing_page_link_only_when_enabled(self):
        self.assertContains(self.client.get('/'), 'My points')
        self.config.enabled = False
        self.config.save()
        self.assertNotContains(self.client.get('/'), 'My points')


class AdminOverviewTests(Base):
    def test_staff_see_overview_and_visitors_do_not(self):
        user = User.objects.create_user(username='boss', password='pw12345!')
        role, _ = Role.objects.get_or_create(name='SUPER_ADMIN')
        StaffProfile.objects.update_or_create(user=user, defaults={'role': role})
        self.give(40)
        self.assertEqual(self.client.get(reverse('core:loyalty')).status_code, 302)
        self.client.force_login(user)
        resp = self.client.get(reverse('core:loyalty'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '1 Hour')
        self.assertEqual(resp.context['outstanding'], 40)


class PastPurchaseTests(Base):
    """The "Include past purchases" setting: old successful payments can earn points too, once."""

    def old_payment(self, days_ago, amount=100, receipt=None, status=Payment.Status.SUCCESS):
        """A payment made while the programme was off, so it earned nothing at the time."""
        self.config.enabled = False
        self.config.save()
        p = self.pay(amount, receipt=receipt or f'OLD{days_ago:07d}', status=status)
        when = timezone.now() - dt.timedelta(days=days_ago)
        Payment.objects.filter(pk=p.pk).update(created_at=when, transaction_timestamp=when)
        self.config.enabled = True
        self.config.save()
        return p

    def turn_on(self, on=True):
        self.config.include_past_purchases = on
        self.config.save()

    def test_off_by_default_so_old_payments_earn_nothing(self):
        self.old_payment(30)
        self.assertFalse(LoyaltyConfig.load().include_past_purchases)
        self.assertEqual(services.snapshot(self.cust)['points'], 0)
        self.assertEqual(PointsLedger.objects.count(), 0)

    def test_on_credits_every_old_successful_payment(self):
        self.old_payment(30, amount=100)   # 10
        self.old_payment(10, amount=250)   # 25
        self.turn_on()
        snap = services.snapshot(self.cust)
        self.assertEqual(snap['points'], 35)
        self.assertTrue(snap['includes_past'])

    def test_each_old_payment_counts_only_once_however_often_it_is_checked(self):
        self.old_payment(30, amount=100)
        self.turn_on()
        for _ in range(4):
            services.snapshot(self.cust)
        self.assertEqual(services.balance(self.cust), 10)
        self.assertEqual(PointsLedger.objects.filter(kind='EARN').count(), 1)

    def test_a_payment_that_already_earned_is_not_counted_twice(self):
        p = self.pay(100)
        services.award_for_payment(p)            # earned live: 10
        self.turn_on()
        services.snapshot(self.cust)
        self.assertEqual(services.balance(self.cust), 10)

    def test_only_confirmed_payments_count(self):
        for i, st in enumerate((Payment.Status.PENDING, Payment.Status.FAILED, Payment.Status.CANCELLED,
                                Payment.Status.TIMEOUT, Payment.Status.REFUNDED)):
            self.old_payment(20, receipt=f'BAD{i:07d}', status=st)
        self.turn_on()
        self.assertEqual(services.snapshot(self.cust)['points'], 0)

    def test_nothing_happens_while_the_whole_programme_is_off(self):
        self.old_payment(30)
        self.turn_on()
        self.config.enabled = False
        self.config.save()
        self.assertEqual(services.sync_past_purchases(self.cust), 0)
        self.assertEqual(PointsLedger.objects.count(), 0)

    def test_old_credits_are_dated_today_by_default_so_they_can_be_redeemed(self):
        self.old_payment(300, amount=100)
        self.turn_on()
        self.assertEqual(services.snapshot(self.cust)['points'], 10)
        entry = PointsLedger.objects.get(kind='EARN')
        self.assertLess(timezone.now() - entry.created_at, dt.timedelta(minutes=5))

    def test_old_credits_are_dated_when_the_customer_really_paid(self):
        self.config.backdate_past_purchases = True
        self.config.save()
        self.old_payment(30, amount=100)
        self.turn_on()
        services.snapshot(self.cust)
        entry = PointsLedger.objects.get(kind='EARN')
        age = timezone.now() - entry.created_at
        self.assertGreater(age, dt.timedelta(days=29))

    def test_purchases_older_than_the_expiry_window_do_not_come_back_to_life(self):
        # Only with backdating ON: a payment 300 days ago (expiry 180) earns nothing usable.
        self.config.backdate_past_purchases = True
        self.config.save()
        self.old_payment(300, amount=100)
        self.turn_on()
        self.assertEqual(services.snapshot(self.cust)['points'], 0)
        self.assertEqual(services.balance(self.cust), 0)

    def test_an_old_purchase_keeps_a_recent_one_alive(self):
        self.config.backdate_past_purchases = True
        self.config.save()
        self.old_payment(300, amount=100)       # 10, but old
        self.old_payment(5, amount=100)         # 10, recent: balance does not lapse
        self.turn_on()
        self.assertEqual(services.snapshot(self.cust)['points'], 20)

    def test_claiming_a_reward_also_counts_past_purchases_first(self):
        self.old_payment(10, amount=300)        # 30 points
        self.turn_on()
        v = services.redeem(self.cust, self.reward.id)
        self.assertEqual(v.customer, self.cust)
        self.assertEqual(services.balance(self.cust), 10)

    def test_a_later_refund_still_takes_the_points_back(self):
        p = self.old_payment(10, amount=100)
        self.turn_on()
        services.snapshot(self.cust)
        self.assertEqual(services.balance(self.cust), 10)
        p.status = Payment.Status.REFUNDED
        p.save()
        self.assertEqual(services.balance(self.cust), 0)

    def test_backfill_all_credits_everyone_and_is_repeatable(self):
        other = Customer.objects.create(full_name='Other', phone_number='254798765432')
        self.old_payment(20, amount=100)
        self.config.enabled = False
        self.config.save()
        p2 = self.pay(50, receipt='OTHER00001', customer=other)
        Payment.objects.filter(pk=p2.pk).update(created_at=timezone.now() - dt.timedelta(days=5))
        self.config.enabled = True
        self.config.save()
        self.turn_on()
        self.assertEqual(services.backfill_all(dry_run=True), {'customers': 2, 'points': 15})
        self.assertEqual(PointsLedger.objects.count(), 0)          # dry run changes nothing
        self.assertEqual(services.backfill_all(), {'customers': 2, 'points': 15})
        self.assertEqual(services.backfill_all(), {'customers': 0, 'points': 0})
        self.assertEqual(services.balance(self.cust), 10)
        self.assertEqual(services.balance(other), 5)

    def test_backfill_all_refuses_while_the_setting_is_off(self):
        with self.assertRaises(services.LoyaltyError):
            services.backfill_all()

    def test_management_command(self):
        from io import StringIO
        from django.core.management import call_command
        self.old_payment(20, amount=100)
        self.turn_on()
        out = StringIO()
        call_command('backfill_loyalty_points', stdout=out)
        self.assertIn('Credited 10 points to 1 customer', out.getvalue())

    def test_admin_action_credits_everyone(self):
        user = User.objects.create_superuser(username='root', password='pw12345!', email='r@example.com')
        self.old_payment(20, amount=100)
        self.turn_on()
        self.client.force_login(user)
        resp = self.client.post('/django-admin/loyalty/loyaltyconfig/', {
            'action': 'credit_past_purchases_now', '_selected_action': [self.config.pk],
        }, follow=True)
        self.assertContains(resp, 'Credited 10 points to 1 customer')
        self.assertEqual(services.balance(self.cust), 10)


class LandingPageSectionTests(Base):
    """The points UI lives on the landing page itself: no redirect to /points/."""
    AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def lookup(self, phone='0712345678'):
        return self.client.post(reverse('loyalty:lookup'), {'phone': phone}, **self.AJAX)

    def test_link_scrolls_to_a_section_on_the_same_page(self):
        html = self.client.get('/').content.decode()
        self.assertIn('href="#points"', html)
        self.assertIn('<section id="points"', html)
        self.assertNotIn(reverse('loyalty:home'), html.split('<section id="points"')[0])   # no link out to /points/
        # The section sits below the Admin Login box.
        self.assertGreater(html.index('<section id="points"'), html.index('id="shareholder-login"'))
        # ...and the voucher box it fills in exists.
        self.assertIn('id="voucher-box"', html)
        self.assertIn('id="voucher-code-input"', html)

    def test_nothing_about_points_is_on_the_page_when_switched_off(self):
        self.config.enabled = False
        self.config.save()
        html = self.client.get('/').content.decode()
        self.assertNotIn('id="points"', html)
        self.assertNotIn('points-lookup-form', html)

    def test_rate_is_printed_as_a_plain_number(self):
        self.assertIn('<span id="points-rate">10</span>', self.client.get('/').content.decode())
        self.assertEqual(services.plain_decimal(Decimal('10.00')), '10')
        self.assertEqual(services.plain_decimal(Decimal('2.50')), '2.5')

    def test_ajax_lookup_returns_json_and_never_redirects(self):
        services.award_for_payment(self.pay(100))
        resp = self.lookup()
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertTrue(d['success'])
        self.assertEqual((d['points'], d['has_phone'], d['verified']), (10, True, False))
        self.assertEqual(d['masked_phone'], '0712 ••• 678')
        self.assertEqual(d['ksh_per_point'], '10')
        self.assertEqual(d['history'], [])
        self.assertIsNone(d['voucher'])

    def test_ajax_lookup_with_a_bad_number(self):
        resp = self.lookup('123')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_ajax_lookup_is_rate_limited(self):
        codes = [self.lookup().status_code for _ in range(22)]
        self.assertEqual(codes[-1], 429)

    def test_the_old_form_post_still_redirects(self):
        resp = self.client.post(reverse('loyalty:lookup'), {'phone': '0712345678'})
        self.assertEqual(resp.status_code, 302)

    def test_unknown_number_looks_like_zero_points_in_json_too(self):
        d = self.lookup('0711000000').json()
        self.assertEqual(d['points'], 0)
        self.assertFalse(Customer.objects.filter(phone_number='254711000000').exists())

    def test_live_shows_history_and_the_unused_voucher_only_after_verifying(self):
        p = self.pay(300)
        services.award_for_payment(p)
        self.lookup()
        before = self.client.get(reverse('loyalty:live')).json()
        self.assertEqual((before['history'], before['voucher']), ([], None))
        self.client.post(reverse('loyalty:verify'), {'code': 'SFH3JT6LKQ'})
        claimed = self.client.post(reverse('loyalty:redeem'), {'reward_id': self.reward.id}).json()
        after = self.client.get(reverse('loyalty:live')).json()
        self.assertTrue(after['verified'])
        self.assertEqual(after['voucher']['code'], claimed['code'])      # still there after a page reload
        self.assertEqual(after['voucher']['package'], '1 Hour')
        self.assertEqual({h['kind'] for h in after['history']}, {'Earned from payment', 'Redeemed for voucher'})

    def test_voucher_disappears_from_the_panel_once_used(self):
        self.give(30)
        self.lookup()
        self.client.post(reverse('loyalty:verify'), {'code': self.pay(10).mpesa_receipt_number})
        d = self.client.post(reverse('loyalty:redeem'), {'reward_id': self.reward.id}).json()
        Voucher.objects.filter(code=d['code']).update(status=Voucher.Status.USED)
        self.assertIsNone(self.client.get(reverse('loyalty:live')).json()['voucher'])

    def test_ajax_forget_clears_the_number(self):
        self.lookup()
        resp = self.client.post(reverse('loyalty:forget'), **self.AJAX)
        self.assertEqual(resp.json(), {'success': True})
        self.assertFalse(self.client.get(reverse('loyalty:live')).json()['has_phone'])

    def test_past_purchases_show_up_in_the_landing_page_panel(self):
        self.config.enabled = False
        self.config.save()
        self.pay(200)
        self.config.enabled = True
        self.config.include_past_purchases = True
        self.config.save()
        d = self.lookup().json()
        self.assertEqual(d['points'], 20)
        self.assertTrue(d['includes_past'])
        self.assertTrue(d['rewards'][0]['affordable'])
