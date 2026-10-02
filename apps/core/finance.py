"""
Company finance: the company wallet, its money-flow ledger, per-shareholder
balances and per-cycle records.

Nothing here is stored separately. The wallet is always recomputed from
records that already exist, so it can never drift out of sync:

    wallet = successful M-Pesa revenue (since tracking began)
           - subscriptions paid        (closed EarningsPeriod.subscription_cost)
           - withdrawals paid          (WithdrawalRequest status=PAID)
           - profit payouts paid       (ShareholderPayout status=PAID)
           +/- manual adjustments      (WalletAdjustment)

"Tracking began" is the start of the very first EarningsPeriod. Money and
withdrawals from before that are ignored so the wallet never mixes in
history the system never recorded; use a WalletAdjustment to enter an
opening balance if you want one.

PRIVACY: ledger_rows(show_amounts=False) is what shareholders get. Only
subscriptions carry an amount there; withdrawals, payouts and adjustments
show just that they happened and the balance left afterwards, with no
amount and no shareholder name.
"""
from decimal import Decimal

from django.db.models import Sum

ZERO = Decimal('0.00')

KIND_LABELS = {
    'SUBSCRIPTION': 'Subscription paid',
    'WITHDRAWAL': 'Shareholder withdrawal',
    'PAYOUT': 'Profit payout',
    'ADJUSTMENT': 'Balance adjustment',
}


def _origin():
    from apps.billing.models import EarningsPeriod
    EarningsPeriod.current()  # make sure at least one period exists
    return EarningsPeriod.objects.order_by('start_at').first()


def _revenue(origin, until=None):
    from apps.billing.models import Payment
    qs = Payment.objects.filter(status=Payment.Status.SUCCESS, created_at__gte=origin.start_at)
    if until is not None:
        qs = qs.filter(created_at__lte=until)
    return qs.aggregate(t=Sum('amount'))['t'] or ZERO


def _events(origin):
    """Every non-revenue movement of money, oldest first. amount is signed (negative = money out)."""
    from apps.billing.models import EarningsPeriod, ShareholderPayout, WalletAdjustment, WithdrawalRequest

    events = []
    for p in EarningsPeriod.objects.filter(end_at__isnull=False):
        if p.subscription_cost:
            events.append({'when': p.end_at, 'kind': 'SUBSCRIPTION', 'amount': -p.subscription_cost,
                           'detail': p.label, 'shareholder': None})
    for w in WithdrawalRequest.objects.filter(
        status=WithdrawalRequest.Status.PAID, period_start__gte=origin.start_date,
    ).select_related('shareholder'):
        events.append({'when': w.decided_at or w.updated_at, 'kind': 'WITHDRAWAL', 'amount': -w.amount,
                       'detail': f'{w.period_start:%d %b %Y} cycle', 'shareholder': w.shareholder})
    for po in ShareholderPayout.objects.filter(
        status=ShareholderPayout.Status.PAID, amount_paid__gt=0,
    ).select_related('shareholder', 'period'):
        events.append({'when': po.paid_at, 'kind': 'PAYOUT', 'amount': -po.amount_paid,
                       'detail': po.period.label, 'shareholder': po.shareholder})
    for a in WalletAdjustment.objects.all():
        events.append({'when': a.created_at, 'kind': 'ADJUSTMENT', 'amount': a.amount,
                       'detail': a.note, 'shareholder': None})
    events.sort(key=lambda e: e['when'])
    return events


def wallet_summary():
    origin = _origin()
    events = _events(origin)

    def total(kind):
        return -sum((e['amount'] for e in events if e['kind'] == kind), ZERO)

    revenue = _revenue(origin)
    adjustments = sum((e['amount'] for e in events if e['kind'] == 'ADJUSTMENT'), ZERO)
    subscriptions, withdrawals, payouts = total('SUBSCRIPTION'), total('WITHDRAWAL'), total('PAYOUT')
    return {
        'tracking_since': origin.start_at,
        'revenue': revenue,
        'subscriptions': subscriptions,
        'withdrawals': withdrawals,
        'payouts': payouts,
        'adjustments': adjustments,
        'balance': revenue - subscriptions - withdrawals - payouts + adjustments,
    }


def ledger_rows(show_amounts, limit=30):
    """
    Newest-first money-flow rows, each with the wallet balance right after
    it happened. show_amounts=False is the shareholder-safe version.
    """
    origin = _origin()
    events = _events(origin)
    running = ZERO
    rows = []
    for e in events:
        running += e['amount']
        rows.append({'event': e, 'cumulative': running})
    rows = rows[-limit:][::-1]

    out = []
    for r in rows:
        e = r['event']
        balance_after = _revenue(origin, until=e['when']) + r['cumulative']
        reveal = show_amounts or e['kind'] == 'SUBSCRIPTION'
        who = e['shareholder'] if show_amounts else None
        out.append({
            'when': e['when'],
            'kind': e['kind'],
            'label': KIND_LABELS[e['kind']],
            'detail': (e['detail'] if (show_amounts or e['kind'] == 'SUBSCRIPTION') else ''),
            'who': who,
            'amount': e['amount'] if reveal else None,
            'balance_after': balance_after,
        })
    return out


def shareholder_balance(sh, origin=None, current_profit=None):
    """
    One shareholder's running balance across ALL cycles - it does not reset
    when a new cycle starts.

        earned    = frozen earnings of every closed cycle + live earnings of the open one
        withdrawn = approved withdrawals + profit paid with "Pay all shareholders"
        pending   = withdrawal requests still awaiting a decision
        remaining = earned - withdrawn
        available = remaining - pending   (what they may still request right now)

    Only cycles the system has tracked count (same cutoff as the wallet).
    """
    from apps.billing.models import EarningsPeriod, Shareholder, ShareholderPayout, WithdrawalRequest

    origin = origin or _origin()
    if current_profit is None:
        current_profit = Shareholder.distributable_profit(EarningsPeriod.current().compute_revenue())

    closed_earned = sh.payouts.aggregate(t=Sum('earnings'))['t'] or ZERO
    earned = closed_earned + sh.earnings_for(current_profit)
    wd_paid = sh.withdrawal_requests.filter(
        status=WithdrawalRequest.Status.PAID, period_start__gte=origin.start_date,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    payouts_paid = sh.payouts.filter(status=ShareholderPayout.Status.PAID).aggregate(t=Sum('amount_paid'))['t'] or ZERO
    pending = sh.withdrawal_requests.filter(
        status=WithdrawalRequest.Status.PENDING, period_start__gte=origin.start_date,
    ).aggregate(t=Sum('amount'))['t'] or ZERO

    withdrawn = wd_paid + payouts_paid
    remaining = earned - withdrawn
    available = remaining - pending
    return {
        'earned': earned, 'withdrawn': withdrawn, 'pending': pending,
        'remaining': remaining, 'available': available if available > 0 else ZERO,
    }


def shareholder_rows():
    """Admin-only: every active shareholder's balance, plus column totals."""
    from apps.billing.models import EarningsPeriod, Shareholder

    origin = _origin()
    current_profit = Shareholder.distributable_profit(EarningsPeriod.current().compute_revenue())
    rows = []
    for sh in Shareholder.objects.filter(is_active=True).select_related('user'):
        row = shareholder_balance(sh, origin, current_profit)
        row['shareholder'] = sh
        rows.append(row)
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ('earned', 'withdrawn', 'pending', 'remaining')}
    return rows, totals


def closed_outstanding(sh, upto_start):
    """
    What a shareholder is still owed from closed cycles up to (and
    including) the one starting at `upto_start`: all their closed earnings
    minus everything already paid or pending to them, whichever cycle it
    was requested in. One shared pool, so a withdrawal taken after a
    Subscribe is never paid a second time by "Pay all shareholders".
    """
    from apps.billing.models import ShareholderPayout, WithdrawalRequest

    origin = _origin()
    closed = sh.payouts.filter(
        period__end_at__isnull=False, period__start_at__lte=upto_start,
    ).aggregate(t=Sum('earnings'))['t'] or ZERO
    wd = sh.withdrawal_requests.filter(
        status__in=[WithdrawalRequest.Status.PAID, WithdrawalRequest.Status.PENDING],
        period_start__gte=origin.start_date,
    ).aggregate(t=Sum('amount'))['t'] or ZERO
    paid = sh.payouts.filter(status=ShareholderPayout.Status.PAID).aggregate(t=Sum('amount_paid'))['t'] or ZERO
    owed = closed - wd - paid
    return owed if owed > 0 else ZERO


def plan_payouts(period, lock=False):
    """
    What "Pay all shareholders" will pay for `period`: [(payout, amount), ...].
    Each shareholder's outstanding balance is spread over their unpaid
    cycles up to this one, oldest first, never more than that cycle's
    earnings. lock=True takes row locks (used inside the pay transaction).
    """
    from apps.billing.models import ShareholderPayout

    qs = ShareholderPayout.objects.filter(
        status=ShareholderPayout.Status.PENDING,
        period__end_at__isnull=False, period__start_at__lte=period.start_at,
    ).select_related('shareholder', 'shareholder__user', 'period').order_by('period__start_at')
    if lock:
        qs = qs.select_for_update(of=('self',))

    by_shareholder = {}
    for payout in qs:
        by_shareholder.setdefault(payout.shareholder_id, []).append(payout)

    plan = []
    for payouts in by_shareholder.values():
        outstanding = closed_outstanding(payouts[0].shareholder, period.start_at)
        for payout in payouts:
            amount = min(payout.earnings, outstanding)
            outstanding -= amount
            plan.append((payout, amount))
    return plan


def shareholder_transactions(sh):
    """
    One shareholder's own statement, oldest first with a running balance,
    returned newest first for display. Earnings are credits; paid
    withdrawals and payouts are debits; pending/rejected requests are
    listed but don't move the balance. Their own figures only - never
    another shareholder's.
    """
    from apps.billing.models import EarningsPeriod, Shareholder, ShareholderPayout, WithdrawalRequest
    from django.utils import timezone

    origin = _origin()
    current = EarningsPeriod.current()
    live_profit = Shareholder.distributable_profit(current.compute_revenue())

    events = []
    for po in sh.payouts.select_related('period'):
        events.append({'when': po.period.end_at, 'kind': 'EARNING', 'amount': po.earnings, 'status': 'Credited',
                       'detail': f'{po.period.label} profit share ({po.percentage}%)', 'moves': True})
        if po.status == ShareholderPayout.Status.PAID and po.amount_paid:
            events.append({'when': po.paid_at, 'kind': 'PAYOUT', 'amount': -po.amount_paid, 'status': 'Paid',
                           'detail': f'{po.period.label} profit paid out', 'moves': True})
    for w in sh.withdrawal_requests.filter(period_start__gte=origin.start_date):
        paid = w.status == WithdrawalRequest.Status.PAID
        events.append({
            'when': (w.decided_at or w.updated_at) if paid else w.created_at,
            'kind': 'WITHDRAWAL', 'amount': -w.amount, 'status': w.get_status_display(),
            'detail': f'Withdrawal to {w.payment_phone}', 'moves': paid,
        })
    live = sh.earnings_for(live_profit)
    events.append({'when': timezone.now(), 'kind': 'EARNING', 'amount': live, 'status': 'In progress',
                   'detail': f'{current.label} (so far)', 'moves': True})

    events.sort(key=lambda e: e['when'])
    balance = ZERO
    for e in events:
        if e['moves']:
            balance += e['amount']
        e['balance_after'] = balance
    return events[::-1]


def period_records():
    """Admin-only table: every closed cycle (newest first), then the open one flagged in_progress."""
    from apps.billing.models import EarningsPeriod, ShareholderPayout, WithdrawalRequest

    out = []
    current = EarningsPeriod.current()
    from apps.billing.models import Shareholder
    live_revenue = current.compute_revenue()
    out.append({
        'period': current, 'in_progress': True, 'revenue': live_revenue,
        'subscription_cost': None,
        'profit': Shareholder.distributable_profit(live_revenue), 'paid_out': None, 'status': 'In progress',
    })
    from apps.core.models import SystemSettings
    out[0]['subscription_cost'] = SystemSettings.load().subscription_cost

    for p in EarningsPeriod.objects.filter(end_at__isnull=False).order_by('-start_at'):
        wd = WithdrawalRequest.objects.filter(
            status=WithdrawalRequest.Status.PAID, period_start=p.start_date,
        ).aggregate(t=Sum('amount'))['t'] or ZERO
        pay = p.payouts.filter(status=ShareholderPayout.Status.PAID).aggregate(t=Sum('amount_paid'))['t'] or ZERO
        unpaid = p.payouts.filter(status=ShareholderPayout.Status.PENDING).exists()
        out.append({
            'period': p, 'in_progress': False, 'revenue': p.revenue,
            'subscription_cost': p.subscription_cost, 'profit': p.distributable_profit,
            'paid_out': wd + pay, 'status': 'Awaiting payout' if unpaid else 'Paid',
        })
    return out
