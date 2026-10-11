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


def plain_decimal(value):
    """10.00 -> '10' (str(Decimal('10.00').normalize()) is '1E+1', which is useless on a web page)."""
    return format(Decimal(value).normalize(), 'f')


def points_for_amount(amount, config):
    """Whole points only (KSh 95 at 10 per point = 9, never rounded up in the customer's favour)."""
    if config.ksh_per_point <= 0:
        return 0
    return int((Decimal(amount) / config.ksh_per_point).to_integral_value(rounding='ROUND_FLOOR'))


def balance(customer):
    return PointsLedger.objects.filter(customer=customer).aggregate(b=Coalesce(Sum('points'), 0))['b']


def award_for_payment(payment, earned_at=None):
    """
    Earn points for ONE confirmed M-Pesa payment. Safe to call repeatedly:
    the unique (payment, kind) constraint means a duplicate callback adds
    nothing. Runs in its own savepoint so a loyalty problem can never roll
    back or break the payment that triggered it.

    earned_at is only used when catching up on OLD payments: the ledger line is
    dated when the customer really paid, so the "points lapse after N days"
    rule treats an old purchase as old instead of giving it a fresh lease of life.
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
            if created and earned_at is not None:
                earned_at = min(earned_at, timezone.now())
                PointsLedger.objects.filter(pk=entry.pk).update(created_at=earned_at)
                entry.created_at = earned_at
            return entry if created else None
    except IntegrityError:
        return None  # a concurrent duplicate got there first


def _unrewarded_payments(customer=None):
    """Successful payments that never earned points (made before the programme / this setting)."""
    qs = Payment.objects.filter(status=Payment.Status.SUCCESS).exclude(points_entries__kind=PointsLedger.Kind.EARN)
    if customer is not None:
        qs = qs.filter(customer=customer)
    return qs


def sync_past_purchases(customer, config=None):
    """
    If the admin allows it ("Include past purchases"), give this customer the points
    for every earlier successful payment that never earned any. Returns points added.

    Safe to call as often as you like (every page view): each payment can earn only
    once (unique constraint), so repeats add nothing, and it does nothing while the
    programme or the setting is off. Call it under the customer row lock.
    """
    config = config or LoyaltyConfig.load()
    if customer is None or not (config.enabled and config.include_past_purchases):
        return 0
    gained = 0
    for payment in _unrewarded_payments(customer).select_related('package').order_by('created_at'):
        entry = award_for_payment(payment, earned_at=payment.transaction_timestamp or payment.created_at)
        if entry:
            gained += entry.points
    return gained


def backfill_all(dry_run=False):
    """
    Apply sync_past_purchases to every customer at once (admin action / management command),
    then lapse any balance that is already stale, so staff totals are right straight away.
    dry_run only counts: {'customers', 'points'} (before any expiry).
    """
    config = LoyaltyConfig.load()
    if not (config.enabled and config.include_past_purchases):
        raise LoyaltyError('Switch the programme on and tick "Include past purchases" first.')
    pending = _unrewarded_payments()
    customers = Customer.objects.filter(pk__in=pending.values('customer_id'))
    total_customers = total_points = 0
    if dry_run:
        for payment in pending.only('amount'):
            pts = points_for_amount(payment.amount, config)
            total_points += pts
        return {'customers': customers.count(), 'points': total_points}
    for customer in customers.iterator():
        with transaction.atomic():
            customer = Customer.objects.select_for_update().get(pk=customer.pk)
            gained = sync_past_purchases(customer, config)
            expire_if_stale(customer)
        if gained:
            total_customers += 1
            total_points += gained
    return {'customers': total_customers, 'points': total_points}


def outstanding_voucher(customer, now=None):
    """The customer's reward voucher that is still unused and unexpired, if any."""
    now = now or timezone.now()
    entry = (PointsLedger.objects
             .filter(customer=customer, kind=PointsLedger.Kind.REDEEM,
                     voucher__status=Voucher.Status.UNUSED, voucher__expiry_date__gt=now)
             .select_related('voucher__package').first())
    return entry.voucher if entry else None


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
            sync_past_purchases(customer, config)
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
            'ksh_per_point': plain_decimal(config.ksh_per_point),
            'expiry_days': config.expiry_days, 'includes_past': config.include_past_purchases}


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

        sync_past_purchases(customer, config)
        expire_if_stale(customer, now)
        current = balance(customer)
        if current < reward.points_cost:
            raise LoyaltyError(f'You need {reward.points_cost - max(current, 0)} more points for this reward.')

        outstanding = outstanding_voucher(customer, now)
        if outstanding:
            raise LoyaltyError(f'You already have an unused reward voucher: {outstanding.code}. '
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
