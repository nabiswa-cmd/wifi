"""
All loyalty rules live here; views and the payment hook stay thin.
Money-safety notes are on each function.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.billing.models import Payment
from apps.billing.utils import extract_mpesa_code
from apps.customers.models import Customer
from apps.vouchers.models import Voucher, generate_code

from .models import LoyaltyConfig, LoyaltyReward, PointsLedger

logger = logging.getLogger(__name__)


class LoyaltyError(Exception):
    """A refusal that is safe to show to the customer as-is."""


def points_for_amount(amount, config):
    """Whole points only (KSh 95 at 10 per point = 9, never rounded up in the customer's favour)."""
    if config.ksh_per_point <= 0:
        return 0
    return int((Decimal(amount) / config.ksh_per_point).to_integral_value(rounding='ROUND_FLOOR'))


def balance(customer):
    return PointsLedger.objects.filter(customer=customer).aggregate(b=Coalesce(Sum('points'), 0))['b']


def award_for_payment(payment):
    """
    Earn points for ONE confirmed M-Pesa payment. Safe to call repeatedly:
    the unique (payment, kind) constraint means a duplicate callback adds
    nothing. Runs in its own savepoint so a loyalty problem can never roll
    back or break the payment that triggered it.
    """
    try:
        with transaction.atomic():
            config = LoyaltyConfig.load()
            if not config.enabled or payment.status != Payment.Status.SUCCESS:
                return None
            points = points_for_amount(payment.amount, config)
            if points <= 0:
                return None
            entry, created = PointsLedger.objects.get_or_create(
                payment=payment, kind=PointsLedger.Kind.EARN,
                defaults={'customer': payment.customer, 'points': points,
                          'note': f'KSh {payment.amount:,.0f} {payment.package.name}'},
            )
            return entry if created else None
    except IntegrityError:
        return None  # a concurrent duplicate got there first


def reverse_for_payment(payment):
    """Refund: take back exactly what that payment earned (balance may go negative if already spent)."""
    with transaction.atomic():
        earned = PointsLedger.objects.filter(payment=payment, kind=PointsLedger.Kind.EARN).first()
        if not earned:
            return None
        entry, created = PointsLedger.objects.get_or_create(
            payment=payment, kind=PointsLedger.Kind.REVERSAL,
            defaults={'customer': earned.customer, 'points': -earned.points, 'note': 'Payment refunded'},
        )
        return entry if created else None


def expire_if_stale(customer, now=None):
    """
    Lapse the whole balance if nothing has been earned for expiry_days.
    Called under the customer lock when redeeming and when the page loads.
    """
    now = now or timezone.now()
    config = LoyaltyConfig.load()
    if not config.expiry_days:
        return 0
    current = balance(customer)
    if current <= 0:
        return 0
    last_earn = (PointsLedger.objects.filter(customer=customer, kind=PointsLedger.Kind.EARN)
                 .order_by('-created_at').values_list('created_at', flat=True).first())
    if last_earn and now - last_earn > timedelta(days=config.expiry_days):
        PointsLedger.objects.create(customer=customer, kind=PointsLedger.Kind.EXPIRE, points=-current,
                                    note=f'No points earned for {config.expiry_days} days')
        return current
    return 0


def verify_ownership(phone, raw_code):
    """
    Proof the person really is the payer: an M-Pesa receipt code from a successful
    payment made BY that phone number (only the payer's SMS contains it).
    Returns the Customer or None. Deliberately gives no hint which half was wrong.
    """
    from apps.billing.utils import normalize_phone_number
    code = extract_mpesa_code(raw_code or '')
    normalized = normalize_phone_number(phone or '')
    if not code or not normalized:
        return None
    customer = Customer.objects.filter(phone_number=normalized).first()
    if not customer:
        return None
    ok = Payment.objects.filter(customer=customer, status=Payment.Status.SUCCESS,
                                mpesa_receipt_number__iexact=code).exists()
    return customer if ok else None


def offerable_rewards(config=None):
    config = config or LoyaltyConfig.load()
    return [r for r in LoyaltyReward.objects.select_related('package') if r.is_offerable(config)]


def snapshot(customer, now=None):
    """Everything the points page needs, in one dict (also what the live endpoint returns)."""
    config = LoyaltyConfig.load()
    if customer is None or not config.enabled:
        points = 0
    else:
        with transaction.atomic():
            Customer.objects.select_for_update().get(pk=customer.pk)
            expire_if_stale(customer, now)
            points = balance(customer)
    rewards = [
        {'id': r.id, 'name': r.package.name, 'cost': r.points_cost,
         'affordable': points >= r.points_cost, 'short_by': max(r.points_cost - points, 0)}
        for r in offerable_rewards(config)
    ]
    rewards.sort(key=lambda r: r['cost'])
    target = next((r for r in rewards if not r['affordable']), None)
    base = 0
    if target:
        prior = [r['cost'] for r in rewards if r['cost'] < target['cost']]
        base = max(prior) if prior else 0
    progress = 100
    if target:
        span = max(target['cost'] - base, 1)
        progress = max(0, min(100, int((points - base) * 100 / span)))
    return {'enabled': config.enabled, 'points': max(points, 0), 'rewards': rewards,
            'next_reward': target, 'progress': progress,
            'ksh_per_point': str(config.ksh_per_point.normalize())}


def redeem(customer, reward_id, now=None):
    """
    Spend points, get a one-time voucher bound to this customer's phone.
    Atomic under a row lock on the customer, so two taps (or two devices)
    can never spend the same points twice.
    """
    now = now or timezone.now()
    with transaction.atomic():
        customer = Customer.objects.select_for_update().get(pk=customer.pk)
        config = LoyaltyConfig.load()
        if not config.enabled:
            raise LoyaltyError('Loyalty rewards are not available right now.')
        if customer.status != Customer.Status.ACTIVE:
            raise LoyaltyError('This account cannot redeem rewards. Please contact support.')
        reward = (LoyaltyReward.objects.select_related('package').filter(pk=reward_id).first())
        if not reward or not reward.is_offerable(config):
            raise LoyaltyError('That reward is not available right now.')

        expire_if_stale(customer, now)
        current = balance(customer)
        if current < reward.points_cost:
            raise LoyaltyError(f'You need {reward.points_cost - max(current, 0)} more points for this reward.')

        outstanding = (PointsLedger.objects
                       .filter(customer=customer, kind=PointsLedger.Kind.REDEEM,
                               voucher__status=Voucher.Status.UNUSED, voucher__expiry_date__gt=now)
                       .select_related('voucher').first())
        if outstanding:
            raise LoyaltyError(f'You already have an unused reward voucher: {outstanding.voucher.code}. '
                               f'Use it first, then redeem again.')

        recent = PointsLedger.objects.filter(customer=customer, kind=PointsLedger.Kind.REDEEM,
                                             created_at__gte=now - timedelta(days=30)).count()
        if recent >= config.max_redemptions_per_30_days:
            raise LoyaltyError('You have reached the limit of rewards for this month. Please try again later.')

        voucher = None
        for _ in range(5):
            try:
                with transaction.atomic():
                    voucher = Voucher.objects.create(
                        code=generate_code(), package=reward.package, customer=customer,
                        status=Voucher.Status.UNUSED,
                        expiry_date=now + timedelta(days=config.voucher_valid_days),
                    )
                break
            except IntegrityError:
                continue
        if voucher is None:
            raise LoyaltyError('Could not create your voucher. Please try again.')

        PointsLedger.objects.create(customer=customer, kind=PointsLedger.Kind.REDEEM,
                                   points=-reward.points_cost, voucher=voucher,
                                   note=f'{reward.package.name} voucher')
        return voucher
