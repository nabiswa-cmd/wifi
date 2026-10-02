import csv
import io
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def admin_login(request):
    """Staff login for the branded dashboard (Section 34)."""
    if request.method == 'POST':
        user = authenticate(
            request,
            username=request.POST.get('username'),
            password=request.POST.get('password'),
        )
        if user is not None and user.is_staff_account:
            login(request, user)
            return redirect('core:dashboard')
        return render(request, 'core/login.html', {'error': 'Invalid credentials'})
    return render(request, 'core/login.html')


def _staff_required(user):
    return user.is_authenticated and getattr(user, 'is_staff_account', False)


def _staff_role_name(user):
    """The acting staff user's Role name, or None if they have no active StaffProfile."""
    profile = getattr(user, 'staff_profile', None)
    if profile is None or not profile.is_active_staff:
        return None
    return profile.role.name


def _is_main_admin(user):
    return _staff_role_name(user) == 'SUPER_ADMIN'


def _can_view_revenue_dashboard(user):
    """Company and Shareholder roles only  see revenue_dashboard's docstring."""
    return user.is_authenticated and _staff_role_name(user) in ('SUPER_ADMIN', 'SHAREHOLDER')


def _link(url_name, **params):
    """reverse(url_name) plus a query string, skipping empty params."""
    from urllib.parse import urlencode
    url = reverse(url_name)
    params = {k: v for k, v in params.items() if v not in (None, '')}
    return f'{url}?{urlencode(params)}' if params else url


@login_required(login_url='core:admin_login')
def dashboard(request):
    """
    Section 20's KPI dashboard, backed by real queries against billing
    and customer data. The reporting-phase charts (Section 23) are now
    built in too   revenue and connection activity over the last 14 days.
    """
    from datetime import timedelta
    from apps.customers.models import Customer
    from apps.billing.models import Payment, Subscription
    from apps.core import customer_insights as insights
    from apps.mikrotik.models import InternetSession

    today = timezone.localdate()

    local_hour = timezone.localtime(timezone.now()).hour
    if local_hour < 12:
        greeting = 'Good morning'
    elif local_hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'
    my_shareholder = getattr(request.user, 'shareholder_profile', None)
    display_name = (
        (my_shareholder.full_name if my_shareholder else '')
        or request.user.get_full_name()
        or request.user.username
    )

    payments_today = Payment.objects.filter(created_at__date=today)
    revenue_today = payments_today.filter(status=Payment.Status.SUCCESS).aggregate(total=Sum('amount'))['total'] or 0

    # Last 14 days, oldest first, for the two trend charts below.
    chart_days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    revenue_by_day = {
        row['created_at__date']: row['total']
        for row in Payment.objects.filter(
            status=Payment.Status.SUCCESS, created_at__date__gte=chart_days[0],
        ).values('created_at__date').annotate(total=Sum('amount'))
    }
    sessions_by_day = {
        row['login_time__date']: row['count']
        for row in InternetSession.objects.filter(
            login_time__date__gte=chart_days[0],
        ).values('login_time__date').annotate(count=Count('id'))
    }

    monthly = (
        Payment.objects.filter(status=Payment.Status.SUCCESS, created_at__gte=today - timedelta(days=180))
        .annotate(month=TruncMonth('created_at'))
        .values('month').annotate(total=Sum('amount'), count=Count('id')).order_by('month')
    )
    monthly_labels = [m['month'].strftime('%b %Y') for m in monthly]
    monthly_totals = [float(m['total']) for m in monthly]

    context = {
        'greeting': greeting,
        'display_name': display_name,
        'monthly_labels': monthly_labels,
        'monthly_totals': monthly_totals,
        'total_customers': Customer.objects.count(),
        'active_customers': insights.count('active_account'),
        # Every customer tile uses customer_insights, the same code the
        # Customers page uses for its list, so a tile's number always equals
        # the number of rows you see after clicking it.
        'expired_customers': insights.count('expired'),
        'online_users': insights.count('online'),
        'lapsed_customers': insights.count('lapsed'),
        'todays_revenue': revenue_today,
        'todays_payments': payments_today.count(),
        'successful_payments': payments_today.filter(status=Payment.Status.SUCCESS).count(),
        'failed_payments': payments_today.filter(
            status__in=[Payment.Status.FAILED, Payment.Status.CANCELLED, Payment.Status.TIMEOUT]
        ).count(),
        'pending_payments': payments_today.filter(status=Payment.Status.PENDING).count(),
        'active_users': insights.count('subscribed'),
        'todays_sessions': InternetSession.objects.filter(login_time__date=today).count(),
        'chart_labels': [d.strftime('%d %b') for d in chart_days],
        'chart_revenue': [float(revenue_by_day.get(d, 0)) for d in chart_days],
        'chart_sessions': [sessions_by_day.get(d, 0) for d in chart_days],
        'tile_links': {
            'customers_all': _link('core:customer_list', filter='all'),
            'customers_active': _link('core:customer_list', filter='active_account'),
            'customers_subscribed': _link('core:customer_list', filter='subscribed'),
            'customers_online': _link('core:customer_list', filter='online'),
            'customers_expired': _link('core:customer_list', filter='expired'),
            'customers_lapsed': _link('core:customer_list', filter='lapsed'),
            'customers_today': _link('core:customer_list', filter='connected_today'),
            'pay_revenue': _link('core:payments', status='SUCCESS', date_from=today.isoformat(), date_to=today.isoformat()),
            'pay_all': _link('core:payments', date_from=today.isoformat(), date_to=today.isoformat()),
            'pay_failed': _link('core:payments', status='FAILED,CANCELLED,TIMEOUT',
                                date_from=today.isoformat(), date_to=today.isoformat()),
            'pay_pending': _link('core:payments', status='PENDING',
                                 date_from=today.isoformat(), date_to=today.isoformat()),
        },
    }
    return render(request, 'core/dashboard.html', context)

def shareholder_login(request):
    """
    Sign-in for the revenue portal  lives as an inline, transparent card
    on the customer landing page itself (right below M-Pesa reconnect /
    voucher redemption), not a separate page. This view only ever needs
    to handle the AJAX POST that card submits (see customers/landing.html);
    a stray GET here (e.g. someone bookmarked the old URL) is bounced back
    to that same section of the landing page rather than shown on its own.

    Both shareholders AND the Company sign in here to reach
    revenue_dashboard directly; anyone else's credentials are rejected
    even if correct, since this form's only job is the revenue portal.
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        user = authenticate(
            request,
            username=request.POST.get('username'),
            password=request.POST.get('password'),
        )
        if user is not None and user.is_staff_account and _can_view_revenue_dashboard(user):
            login(request, user)
            from apps.core.models import AuditLog
            AuditLog.objects.create(
                actor=user, action='PORTAL_LOGIN', object_type='User', object_id=str(user.id),
                new_value={'role': _staff_role_name(user)},
                ip_address=request.META.get('REMOTE_ADDR'),
            )
            redirect_url = reverse('core:revenue_dashboard')
            if is_ajax:
                return JsonResponse({'success': True, 'redirect': redirect_url})
            return redirect(redirect_url)

        error = 'Invalid credentials, or this account is not set up for the shareholder revenue portal.'
        if is_ajax:
            return JsonResponse({'success': False, 'error': error}, status=400)
        messages.error(request, error)
        return redirect(f"{reverse('customers:landing')}#shareholder-login")

    # No standalone login page anymore  send GET requests back to the
    # landing page's own login card.
    return redirect(f"{reverse('customers:landing')}#shareholder-login")


def logout_view(request):
    """
    Logs any staff/shareholder account out and takes them straight back to
    the customer landing page (never to a bare login page of their own),
    same section as sign-in so it feels like one continuous flow.
    """
    logout(request)
    return redirect(f"{reverse('customers:landing')}#shareholder-login")


@login_required(login_url='core:shareholder_login')
def revenue_dashboard(request):
    """
    Revenue Visibility for All Shareholders.

    Open to Role.SUPER_ADMIN (Company) and Role.SHAREHOLDER only 
    everyone else gets a 403, matching "Shareholders can see the
    company's overall revenue... The Company can see everything".

    Every figure here is built from Payment rows with status=SUCCESS
    only  failed/pending/cancelled/timeout/refunded payments never
    enter revenue_qs, so they can never leak into any total below.

    "Financial month" is taken to mean the calendar month; if the
    business runs a different financial-period cutoff (e.g. 25th-24th),
    only the two date-range calculations below need to change.
    """
    from datetime import timedelta

    from apps.billing.models import Payment, Shareholder
    from apps.core.models import SystemSettings

    if not _can_view_revenue_dashboard(request.user):
        return HttpResponse('Forbidden: revenue dashboard is restricted to shareholders and the Company.', status=403)

    from apps.billing.models import EarningsPeriod

    is_main_admin = _is_main_admin(request.user)
    now = timezone.now()
    today = timezone.localdate()

    # The "month" is the Company-controlled billing cycle (EarningsPeriod),
    # not the calendar month: day one is whenever Subscribe was last pressed.
    period = EarningsPeriod.current()
    month_start = period.start_date
    previous_period = EarningsPeriod.objects.filter(end_at__isnull=False).order_by('-start_at').first()

    revenue_qs = Payment.objects.filter(status=Payment.Status.SUCCESS)

    todays_revenue = revenue_qs.filter(created_at__date=today).aggregate(t=Sum('amount'))['t'] or 0
    todays_payment_count = revenue_qs.filter(created_at__date=today).count()

    period_qs = revenue_qs.filter(created_at__gte=period.start_at)
    month_agg = period_qs.aggregate(total=Sum('amount'), count=Count('id'))
    company_revenue = month_agg['total'] or 0
    month_payment_count = month_agg['count'] or 0

    prev_month_revenue = (previous_period.revenue if previous_period else 0) or 0

    if prev_month_revenue:
        growth_pct = float((company_revenue - prev_month_revenue) / prev_month_revenue * 100)
    else:
        growth_pct = 100.0 if company_revenue else 0.0

    # Daily curve across the current cycle so far.
    days_so_far = [month_start + timedelta(days=i) for i in range(max((today - month_start).days, 0) + 1)]
    revenue_by_day = {
        row['created_at__date']: row['total']
        for row in period_qs.values('created_at__date').annotate(total=Sum('amount'))
    }
    daily_chart_labels = [d.strftime('%d %b') for d in days_so_far]
    daily_chart_revenue = [float(revenue_by_day.get(d, 0)) for d in days_so_far]

    # Historical performance: previous closed cycles, oldest first.
    past_periods = list(EarningsPeriod.objects.filter(end_at__isnull=False).order_by('-start_at')[:12])[::-1]
    historical_labels = [p.label for p in past_periods]
    historical_totals = [float(p.revenue or 0) for p in past_periods]

    distributable_profit = Shareholder.distributable_profit(company_revenue)
    total_capital = SystemSettings.load().total_capital
    subscription_cost = SystemSettings.load().subscription_cost

    # Greeting for whoever just landed on the portal  varies with the time
    # of day, Africa/Nairobi local time either way the server is deployed.
    local_hour = timezone.localtime(now).hour
    if local_hour < 12:
        greeting = 'Good morning'
    elif local_hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'

    my_shareholder = getattr(request.user, 'shareholder_profile', None)
    display_name = (
        (my_shareholder.full_name if my_shareholder else '')
        or request.user.get_full_name()
        or request.user.username
    )

    context = {
        'is_main_admin': is_main_admin,
        'greeting': greeting,
        'display_name': display_name,
        'subscription_cost': subscription_cost,
        'total_capital': total_capital,
        'todays_revenue': todays_revenue,
        'todays_payment_count': todays_payment_count,
        'cycle_hint': f'Revenue so far this cycle. Day {period.day_number}, started {timezone.localtime(period.start_at):%d %b %Y}.',
        'previous_tile_label': previous_period.label if previous_period else 'Previous Cycle',
        'tile_links': {
            'today': _link('core:payments', status='SUCCESS', date_from=today.isoformat(), date_to=today.isoformat()),
            'cycle': _link('core:payments', status='SUCCESS', cycle='current'),
            'previous': _link('core:payments', status='SUCCESS', cycle='previous') if previous_period else '',
        },
        'company_revenue': company_revenue,
        'month_payment_count': month_payment_count,
        'prev_month_revenue': prev_month_revenue,
        'growth_pct': round(growth_pct, 1),
        'distributable_profit': distributable_profit,
        'daily_chart_labels': daily_chart_labels,
        'daily_chart_revenue': daily_chart_revenue,
        'historical_labels': historical_labels,
        'historical_totals': historical_totals,
        'current_month_label': period.label,
        'period': period,
        'period_day': period.day_number,
        'previous_period_label': previous_period.label if previous_period else '',
    }

    from apps.billing.models import WithdrawalRequest

    # My own earnings + withdrawal standing  applies to any logged-in
    # user with a linked Shareholder row, Company included (he "can
    # also be a shareholder"), not just the SHAREHOLDER-role branch below.
    context['my_shareholder'] = my_shareholder
    context['my_earnings'] = my_shareholder.earnings_for(distributable_profit) if my_shareholder else None
    if my_shareholder:
        # Running balance across ALL cycles - Subscribe starting a new cycle
        # must never reset what a shareholder can still withdraw.
        from apps.core import finance as _fin
        bal = _fin.shareholder_balance(my_shareholder, current_profit=distributable_profit)
        context['withdrawal_period'] = month_start
        context['my_balance'] = bal
        context['available_to_withdraw'] = bal['available']
        context['my_withdrawals'] = my_shareholder.withdrawal_requests.order_by('-created_at')[:15]

    from apps.billing.models import ShareIncreaseRequest

    if is_main_admin:
        # Full distribution: every shareholder's own figures, plus each
        # other's  visible only here, never from the SHAREHOLDER branch.
        rows = []
        for sh in Shareholder.objects.filter(is_active=True).select_related('user'):
            rows.append({
                'shareholder': sh,
                'earnings': sh.earnings_for(distributable_profit),
            })
        context['shareholder_rows'] = rows
        from apps.billing.models import EarningsPeriod as _EP, ShareholderPayout as _SP
        closed = list(_EP.objects.filter(end_at__isnull=False).order_by('-start_at')[:6])
        from apps.core import finance as _fin2
        for cp in closed:
            cp.payout_rows = list(cp.payouts.select_related('shareholder', 'shareholder__user'))
            cp.unpaid_count = sum(1 for r in cp.payout_rows if r.status == _SP.Status.PENDING)
            planned = {po.pk: amt for po, amt in _fin2.plan_payouts(cp)} if cp.unpaid_count else {}
            for r in cp.payout_rows:
                r.due_now = planned.get(r.pk, 0) if r.status == _SP.Status.PENDING else r.amount_paid
        context['closed_periods'] = closed
        from apps.vouchers.models import VoucherBatch
        context['pending_voucher_requests'] = VoucherBatch.objects.filter(
            approval_status=VoucherBatch.ApprovalStatus.PENDING
        ).count()
        # Withdrawal requests moved to their own Main-Admin-only sidebar
        # page (core:withdrawal_requests_admin)  no longer shown here.
        # Share-increase requests awaiting a decision  approving one is
        # the only thing that ever moves a shareholder's share_quantity,
        # contribution or the company's total_capital (see
        # ShareIncreaseRequest.approve).
        context['pending_share_increases'] = ShareIncreaseRequest.objects.filter(
            status=ShareIncreaseRequest.Status.PENDING
        ).select_related('shareholder', 'shareholder__user').order_by('created_at')
        context['recent_share_increase_decisions'] = ShareIncreaseRequest.objects.exclude(
            status=ShareIncreaseRequest.Status.PENDING
        ).select_related('shareholder', 'decided_by').order_by('-decided_at')[:15]
    else:
        # Every shareholder can see who the other shareholders are and
        # their percentage share  but never their contribution amount
        # or earnings (those two stay Main-Admin-only, above).
        context['all_shareholders'] = list(
            Shareholder.objects.filter(is_active=True).select_related('user').only(
                'id', 'full_name', 'percentage', 'user__username'
            )
        )

    if my_shareholder:
        # A shareholder's own history of share-increase requests  read
        # only, same as everything else about share_quantity/contribution;
        # the request form (request_share_increase) is the only write path.
        context['my_share_increase_requests'] = my_shareholder.share_increase_requests.order_by('-created_at')[:15]

    # Company wallet: visible to every shareholder, but with NO withdrawal
    # amounts or names - only the balance, and amounts for subscriptions.
    from apps.core import finance as fin
    context['wallet_balance'] = fin.wallet_summary()['balance']
    context['wallet_ledger'] = fin.ledger_rows(show_amounts=False, limit=15)

    return render(request, 'core/revenue_dashboard.html', context)


@login_required(login_url='core:shareholder_login')
@require_POST
def request_withdrawal(request):
    """
    A shareholder (or the Company, if he's also a shareholder) asks to
    withdraw some of their available earnings, dropping in the payment
    details for THIS payout each time. Capped at what's actually left
    unclaimed for the current calendar month so the same earnings can't be
    requested twice.
    """
    from apps.billing.models import Payment, Shareholder, WithdrawalRequest

    my_shareholder = getattr(request.user, 'shareholder_profile', None)
    back = f"{reverse('core:revenue_dashboard')}#my-earnings"

    if not my_shareholder:
        messages.error(request, 'No shareholder record is linked to your account.')
        return redirect(back)

    from apps.billing.models import EarningsPeriod

    from apps.core import finance as fin

    period = EarningsPeriod.current()
    month_start = period.start_date   # the cycle the request is recorded against
    available = fin.shareholder_balance(my_shareholder)['available']   # across ALL cycles

    phone = (request.POST.get('payment_phone') or '').strip()
    account_name = (request.POST.get('payment_account_name') or '').strip()
    note = (request.POST.get('note') or '').strip()

    try:
        amount = Decimal(request.POST.get('amount', '').strip())
    except (InvalidOperation, ValueError, AttributeError):
        messages.error(request, 'Enter a valid withdrawal amount.')
        return redirect(back)

    if amount <= 0:
        messages.error(request, 'Enter a withdrawal amount greater than zero.')
    elif amount > available:
        messages.error(request, f'You can withdraw up to {available}.')
    elif not phone:
        messages.error(request, 'Enter the phone number the payout should be sent to.')
    else:
        withdrawal = WithdrawalRequest.objects.create(
            shareholder=my_shareholder, amount=amount, period_start=month_start,
            payment_phone=phone, payment_account_name=account_name, note=note,
        )
        from apps.core.emails import send_new_withdrawal_admin_notification
        send_new_withdrawal_admin_notification(withdrawal)
        # Keep the shareholder's saved default payout details (the ones
        # that pre-fill this very form  see my_account/payment_phone) in
        # sync with whatever they just used, so an edit made here "for
        # this request" also becomes the new default next time.
        update_fields = []
        if phone != my_shareholder.payment_phone:
            my_shareholder.payment_phone = phone
            update_fields.append('payment_phone')
        if account_name != my_shareholder.payment_account_name:
            my_shareholder.payment_account_name = account_name
            update_fields.append('payment_account_name')
        if update_fields:
            my_shareholder.save(update_fields=update_fields + ['updated_at'])

        messages.success(request, f'Withdrawal request for {amount} submitted  awaiting Company approval.')

    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def decide_withdrawal(request, request_id):
    """
    Main-Admin-only approve/reject action for a shareholder's withdrawal
    request. Approving means "I have sent the money"  see
    WithdrawalRequest.approve_and_pay  since payouts are manual for now.
    """
    from apps.billing.models import WithdrawalRequest

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    withdrawal = get_object_or_404(
        WithdrawalRequest, pk=request_id, status=WithdrawalRequest.Status.PENDING
    )
    action = request.POST.get('action')
    back = reverse('core:withdrawal_requests_admin')

    from apps.core.emails import send_withdrawal_approved_email, send_withdrawal_rejected_email

    if action == 'approve':
        withdrawal.approve_and_pay(request.user)
        send_withdrawal_approved_email(withdrawal)
        messages.success(request, f'Marked paid: {withdrawal.amount} to {withdrawal.shareholder}.')
    elif action == 'reject':
        reason = request.POST.get('reason', '').strip()
        withdrawal.reject(request.user, reason=reason)
        send_withdrawal_rejected_email(withdrawal)
        messages.success(request, f'Rejected withdrawal request from {withdrawal.shareholder}.')
    else:
        messages.error(request, 'Unknown action.')

    return redirect(back)


@login_required(login_url='core:admin_login')
def my_account(request):
    """
    A shareholder's own account page: the only place they can change
    anything about themselves  their display name, email, password, and
    their default payout details (auto-filled onto every new withdrawal
    request, see request_withdrawal).

    Deliberately does NOT expose share_quantity, contribution or
    percentage as editable fields anywhere on this page: those three only
    ever move through an approved ShareIncreaseRequest (see
    request_share_increase/decide_share_increase below), never by direct
    edit, so they can't drift out of sync with total_capital.
    """
    from apps.billing.models import Shareholder

    my_shareholder = getattr(request.user, 'shareholder_profile', None)

    if request.method == 'POST' and request.POST.get('form') == 'profile':
        full_name = (request.POST.get('full_name') or '').strip()
        email = (request.POST.get('email') or '').strip()
        payment_phone = (request.POST.get('payment_phone') or '').strip()
        payment_account_name = (request.POST.get('payment_account_name') or '').strip()

        previous_email = request.user.email
        request.user.email = email
        request.user.save(update_fields=['email'])

        if my_shareholder:
            my_shareholder.full_name = full_name
            my_shareholder.payment_phone = payment_phone
            my_shareholder.payment_account_name = payment_account_name
            my_shareholder.save(update_fields=[
                'full_name', 'payment_phone', 'payment_account_name', 'updated_at',
            ])

        # New/changed email  send the welcome email confirming they're on
        # file as part of the Company, same as first joining.
        if email and email != previous_email:
            from apps.core.emails import send_welcome_email
            send_welcome_email(request.user, shareholder=my_shareholder)

        messages.success(request, 'Your details have been updated.')
        return redirect('core:my_account')

    if request.method == 'POST' and request.POST.get('form') == 'password':
        from django.contrib.auth import update_session_auth_hash
        from django.contrib.auth.forms import PasswordChangeForm

        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)  # don't force them to log back in
            messages.success(request, 'Your password has been changed.')
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
        return redirect('core:my_account')

    context = {
        'my_shareholder': my_shareholder,
    }
    if my_shareholder:
        context['my_share_increase_requests'] = my_shareholder.share_increase_requests.order_by('-created_at')[:15]
    return render(request, 'core/my_account.html', context)


@login_required(login_url='core:shareholder_login')
@require_POST
def request_share_increase(request):
    """
    A shareholder asks to grow their stake by contributing more capital.
    This only ever queues a ShareIncreaseRequest for the Company to
    approve or reject  nothing about the shareholder's own row (or the
    company's total_capital) changes until that decision is made (see
    ShareIncreaseRequest.approve / decide_share_increase).
    """
    from apps.billing.models import ShareIncreaseRequest

    my_shareholder = getattr(request.user, 'shareholder_profile', None)
    back = f"{reverse('core:revenue_dashboard')}#increase-shares"

    if not my_shareholder:
        messages.error(request, 'No shareholder record is linked to your account.')
        return redirect(back)

    try:
        share_quantity = int((request.POST.get('share_quantity') or '').strip())
    except (TypeError, ValueError):
        share_quantity = 0

    try:
        contribution_amount = Decimal((request.POST.get('contribution_amount') or '').strip())
    except (InvalidOperation, ValueError, AttributeError):
        messages.error(request, 'Enter a valid contribution amount.')
        return redirect(back)

    note = (request.POST.get('note') or '').strip()

    if share_quantity <= 0:
        messages.error(request, 'Enter the number of additional shares you want, greater than zero.')
    elif contribution_amount <= 0:
        messages.error(request, 'Enter a contribution amount greater than zero.')
    else:
        ShareIncreaseRequest.objects.create(
            shareholder=my_shareholder, share_quantity=share_quantity,
            contribution_amount=contribution_amount, note=note,
        )
        messages.success(
            request,
            f'Request to add {share_quantity} share(s) for {contribution_amount} submitted  '
            'awaiting Company approval.',
        )

    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def decide_share_increase(request, request_id):
    """
    Main-Admin-only approve/reject action for a shareholder's request to
    increase their shares. Approving is the only way a shareholder's
    share_quantity/contribution, and the company's total_capital, ever
    change after they're first set  see ShareIncreaseRequest.approve.
    """
    from apps.billing.models import ShareIncreaseRequest

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    increase_request = get_object_or_404(
        ShareIncreaseRequest, pk=request_id, status=ShareIncreaseRequest.Status.PENDING
    )
    action = request.POST.get('action')
    back = f"{reverse('core:revenue_dashboard')}#share-increase-requests"

    if action == 'approve':
        increase_request.approve(request.user)
        from apps.core.emails import send_share_increase_approved_email
        send_share_increase_approved_email(increase_request)
        messages.success(
            request,
            f'Approved: +{increase_request.share_quantity} share(s) for {increase_request.shareholder}, '
            f'total capital increased by {increase_request.contribution_amount}. '
            'Every shareholder has been emailed.',
        )
    elif action == 'reject':
        reason = request.POST.get('reason', '').strip()
        increase_request.reject(request.user, reason=reason)
        messages.success(request, f'Rejected share-increase request from {increase_request.shareholder}.')
    else:
        messages.error(request, 'Unknown action.')

    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def subscribe_new_period(request):
    """
    Main-Admin-only "Subscribe" button. Marks the moment the platform
    subscription was paid: closes the open earnings period (freezing its
    revenue, subscription cost, distributable profit and every
    shareholder's share of it) and opens the next one, so day one of the
    next 30-day cycle starts exactly when the Company says, not on the 1st.

    Optional form fields:
      start_at - "datetime-local" for day one; blank = right now. Can be
                 in the past (subscribed yesterday) but not the future.
      label    - override for the default "<Month> earnings" name.
    """
    import datetime as dt

    from django.db import transaction
    from apps.billing.models import EarningsPeriod, Shareholder, ShareholderPayout
    from apps.core.models import SystemSettings

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    back = reverse('core:revenue_dashboard')
    now = timezone.now()

    raw_start = (request.POST.get('start_at') or '').strip()
    if raw_start:
        try:
            new_start = timezone.make_aware(dt.datetime.fromisoformat(raw_start))
        except ValueError:
            messages.error(request, 'That start date/time was not valid.')
            return redirect(back)
    else:
        new_start = now

    if new_start > now + dt.timedelta(minutes=1):
        messages.error(request, 'Day one cannot be in the future - pick now or an earlier time.')
        return redirect(back)

    with transaction.atomic():
        period = (
            EarningsPeriod.objects.select_for_update()
            .filter(end_at__isnull=True).order_by('-start_at').first()
        ) or EarningsPeriod.current()

        if timezone.localtime(new_start).date() <= period.start_date:
            messages.error(
                request,
                f'The new cycle must start after {period.label} began '
                f'({period.start_date:%d %b %Y}).',
            )
            return redirect(back)

        revenue = period.compute_revenue(until=new_start)
        profit = Shareholder.distributable_profit(revenue)

        period.end_at = new_start
        period.revenue = revenue
        period.subscription_cost = SystemSettings.load().subscription_cost
        period.distributable_profit = profit
        period.closed_by = request.user
        period.save()

        for sh in Shareholder.objects.filter(is_active=True):
            ShareholderPayout.objects.update_or_create(
                period=period, shareholder=sh,
                defaults={'percentage': sh.percentage, 'earnings': sh.earnings_for(profit)},
            )

        label = (request.POST.get('label') or '').strip()[:100] or EarningsPeriod.default_label(new_start)
        EarningsPeriod.objects.create(label=label, start_at=new_start)

    from apps.core.audit import log_action
    log_action(request.user, 'SUBSCRIBE_NEW_PERIOD', period,
               new_value={'closed': period.label, 'revenue': str(revenue), 'profit': str(profit),
                          'next_start': new_start.isoformat(), 'next_label': label})

    messages.success(
        request,
        f'Subscribed. {period.label} is closed (revenue {revenue}, distributable profit {profit}) '
        f'and {label} starts now at day 1.',
    )
    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def pay_all_shareholders(request):
    """
    Main-Admin-only "Pay all shareholders" button. Marks every unpaid
    shareholder payout of the chosen CLOSED period as paid and emails each
    shareholder the amount that was paid to them.

    Payouts are manual (the Company sends the M-Pesa himself, then presses
    this), same convention as WithdrawalRequest.approve_and_pay. What is
    paid is each shareholder's frozen earnings for the period minus
    anything they already withdrew or have pending for it, so nobody is
    paid twice for the same money. Pressing it twice is harmless: only
    payouts still marked unpaid are touched.
    """
    from django.db import transaction
    from apps.billing.models import EarningsPeriod, ShareholderPayout
    from apps.core.emails import send_profit_payout_email

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    back = reverse('core:revenue_dashboard')
    try:
        period_id = int(request.POST.get('period_id', ''))
    except ValueError:
        messages.error(request, 'No period selected.')
        return redirect(back)

    from apps.core import finance as fin

    paid = []
    with transaction.atomic():
        period = get_object_or_404(
            EarningsPeriod.objects.select_for_update(), pk=period_id, end_at__isnull=False
        )
        now = timezone.now()
        touched = {period.pk: period}
        for payout, amount in fin.plan_payouts(period, lock=True):
            payout.amount_paid = amount
            payout.status = ShareholderPayout.Status.PAID
            payout.paid_at = now
            payout.save(update_fields=['amount_paid', 'status', 'paid_at'])
            paid.append(payout)
            touched[payout.period_id] = payout.period
        for p in touched.values():
            if not p.payouts.filter(status=ShareholderPayout.Status.PENDING).exists() and not p.paid_out_at:
                p.paid_out_at = now
                p.paid_out_by = request.user
                p.save(update_fields=['paid_out_at', 'paid_out_by'])

    if not paid:
        messages.info(request, f'Nothing to pay - everyone for {period.label} is already marked paid.')
        return redirect(back)

    emailed = skipped = 0
    for payout in paid:
        if payout.amount_paid <= 0:
            continue   # fully withdrawn already - nothing to tell them
        if payout.shareholder.user.email:
            send_profit_payout_email(payout)
            payout.emailed_at = timezone.now()
            payout.save(update_fields=['emailed_at'])
            emailed += 1
        else:
            skipped += 1

    total = sum((p.amount_paid for p in paid), Decimal('0.00'))
    from apps.core.audit import log_action
    log_action(request.user, 'PAY_ALL_SHAREHOLDERS', period,
               new_value={'period': period.label, 'total': str(total), 'shareholders': len(paid)})

    msg = f'{period.label}: marked {len(paid)} shareholder(s) paid, {total} in total. Emailed {emailed}.'
    if skipped:
        msg += f' {skipped} had no email on file - add one on their account so they get notified next time.'
    messages.success(request, msg)
    return redirect(back)


@login_required(login_url='core:shareholder_login')
def my_transactions(request):
    """
    A shareholder's own statement: profit credited each cycle, withdrawals
    (pending / paid / rejected) and profit payouts, with a running balance.
    Strictly their own record - it is built from request.user's Shareholder
    row only, so nobody can see anyone else's transactions here.
    """
    from apps.core import finance as fin

    if not _can_view_revenue_dashboard(request.user):
        return HttpResponse('Forbidden.', status=403)

    sh = getattr(request.user, 'shareholder_profile', None)
    if sh is None:
        messages.error(request, 'No shareholder record is linked to your account.')
        return redirect('core:revenue_dashboard')

    return render(request, 'core/my_transactions.html', {
        'my_shareholder': sh,
        'balance': fin.shareholder_balance(sh),
        'transactions': fin.shareholder_transactions(sh),
    })


@login_required(login_url='core:admin_login')
def finance(request):
    """
    Main-Admin-only Finance page: the company wallet with full amounts,
    what each shareholder has earned / withdrawn / has left, and the
    per-cycle records table (revenue, subscription, profit, payout status).
    Shareholders never reach this page - they only get the amount-free
    wallet card on their revenue dashboard (see revenue_dashboard).
    """
    from apps.core import finance as fin

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden: finance is restricted to the Company.', status=403)

    sh_rows, sh_totals = fin.shareholder_rows()
    return render(request, 'core/finance.html', {
        'wallet': fin.wallet_summary(),
        'ledger': fin.ledger_rows(show_amounts=True, limit=50),
        'shareholder_rows': sh_rows,
        'shareholder_totals': sh_totals,
        'records': fin.period_records(),
    })


@login_required(login_url='core:admin_login')
@require_POST
def add_wallet_adjustment(request):
    """Main-Admin-only manual wallet correction (signed amount + required note)."""
    from apps.billing.models import WalletAdjustment
    from apps.core.audit import log_action

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    back = reverse('core:finance')
    note = (request.POST.get('note') or '').strip()[:255]
    try:
        amount = Decimal((request.POST.get('amount') or '').strip())
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter a valid amount (use a minus sign to reduce the wallet).')
        return redirect(back)
    if amount == 0:
        messages.error(request, 'The adjustment cannot be zero.')
    elif not note:
        messages.error(request, 'Add a short note explaining the adjustment.')
    else:
        adj = WalletAdjustment.objects.create(amount=amount, note=note, created_by=request.user)
        log_action(request.user, 'WALLET_ADJUSTMENT', adj, new_value={'amount': str(amount), 'note': note})
        messages.success(request, f'Wallet adjusted by {amount}.')
    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def update_total_capital(request):
    """
    Main-Admin-only: sets the total amount the business has spent/
    invested. Every Shareholder.percentage is derived from this figure
    (contribution / total_capital x 100) the next time it's saved, so
    changing this alone doesn't retroactively touch existing rows 
    re-save each Shareholder (e.g. via Django Admin) to refresh them.
    """
    from apps.core.models import SystemSettings

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    try:
        amount = Decimal(request.POST.get('total_capital', '').strip())
        if amount < 0:
            raise ValueError
    except (InvalidOperation, ValueError, AttributeError):
        messages.error(request, 'Enter a valid, non-negative amount.')
        return redirect('core:revenue_dashboard')

    settings_row = SystemSettings.load()
    settings_row.total_capital = amount
    settings_row.save(update_fields=['total_capital', 'updated_at'])

    # Re-save every shareholder so their percentage recalculates against
    # the new total straight away, instead of only on their next edit.
    from apps.billing.models import Shareholder
    for sh in Shareholder.objects.all():
        sh.save(update_fields=['percentage', 'updated_at'])

    messages.success(request, f'Total capital updated to {settings_row.total_capital}.')
    return redirect('core:revenue_dashboard')
@login_required(login_url='core:admin_login')
@require_POST
def update_subscription_cost(request):
    from apps.core.models import SystemSettings
    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)
    try:
        amount = Decimal(request.POST.get('subscription_cost', '').strip())
        if amount < 0:
            raise ValueError
    except (InvalidOperation, ValueError, AttributeError):
        messages.error(request, 'Enter a valid, non-negative amount.')
        return redirect('core:revenue_dashboard')
    settings_row = SystemSettings.load()
    settings_row.subscription_cost = amount
    settings_row.save(update_fields=['subscription_cost', 'updated_at'])
    messages.success(request, f'Subscription cost updated to {settings_row.subscription_cost}.')
    return redirect('core:revenue_dashboard')

@login_required(login_url='core:admin_login')
def payment_management(request):
    """Section 21: filterable payment list + CSV export."""
    from apps.billing.models import Payment

    qs = Payment.objects.select_related('customer', 'package').order_by('-created_at')
    params = request.GET
    statuses = [x for x in (params.get('status') or '').split(',') if x]
    if statuses:
        qs = qs.filter(status__in=statuses)
    cycle = params.get('cycle')
    cycle_label = ''
    if cycle in ('current', 'previous'):
        from apps.billing.models import EarningsPeriod
        if cycle == 'current':
            period = EarningsPeriod.current()
            qs = qs.filter(created_at__gte=period.start_at)
            cycle_label = f'{period.label} (current cycle)'
        else:
            period = EarningsPeriod.objects.filter(end_at__isnull=False).order_by('-start_at').first()
            if period is not None:
                qs = qs.filter(created_at__gte=period.start_at, created_at__lt=period.end_at)
                cycle_label = f'{period.label} (previous cycle)'
    if params.get('phone'):
        qs = qs.filter(phone_number__icontains=params['phone'])
    if params.get('date_from'):
        qs = qs.filter(created_at__date__gte=params['date_from'])
    if params.get('date_to'):
        qs = qs.filter(created_at__date__lte=params['date_to'])

    if params.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="payments.csv"'
        writer = csv.writer(response)
        writer.writerow(['Customer', 'Phone', 'Amount', 'Package', 'Status', 'Receipt', 'Date'])
        for p in qs:
            writer.writerow([p.customer.full_name, p.phone_number, p.amount, p.package.name,
                              p.status, p.mpesa_receipt_number or '', p.created_at])
        return response

    totals = qs.aggregate(
        total_revenue=Sum('amount', filter=Q(status='SUCCESS')),
        successful=Count('id', filter=Q(status='SUCCESS')),
        failed=Count('id', filter=Q(status__in=['FAILED', 'CANCELLED', 'TIMEOUT'])),
        pending=Count('id', filter=Q(status='PENDING')),
    )
    return render(request, 'core/payments.html', {
        'payments': qs[:200], 'totals': totals,
        'selected_status': ','.join(statuses),
        'cycle_label': cycle_label,
        'filters_active': bool(statuses or cycle_label or params.get('phone')
                               or params.get('date_from') or params.get('date_to')),
    })


@login_required(login_url='core:admin_login')
def subscription_management(request):
    """
    Section 22: full subscription/entitlement history, never overwritten.

    Search (name / phone / M-Pesa receipt), status, package and "customer's
    last purchase" filters, plus sorting so you can see who bought most
    recently or who has not bought for the longest. Each row shows how many
    times that customer has purchased.
    """
    from django.core.paginator import Paginator
    from django.db.models import F
    from apps.billing.models import Subscription
    from apps.core import customer_insights as insights
    from apps.core import time_adjustments as ta
    from apps.packages.models import InternetPackage

    if _staff_role_name(request.user) is None:
        return HttpResponse('Forbidden.', status=403)

    params = request.GET
    now = timezone.now()
    qs = Subscription.objects.select_related('customer', 'package', 'payment').annotate(
        **{('cust_' + k): v for k, v in insights.purchase_annotations('customer_id').items()}
    )

    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(customer__full_name__icontains=q) | Q(customer__phone_number__icontains=q)
            | Q(payment__mpesa_receipt_number__icontains=q) | Q(package__name__icontains=q)
        )
    status = params.get('status') or ''
    if status in Subscription.Status.values:
        qs = qs.filter(status=status)
    package = params.get('package') or ''
    if package.isdigit():
        qs = qs.filter(package_id=int(package))
    last = params.get('last') or ''
    cond = insights.recency_q(last, field='cust_last_purchase', now=now) if last else None
    if cond is not None:
        qs = qs.filter(cond)

    sort = params.get('sort') or 'newest'
    ordering = {
        'newest': ['-created_at'],
        'oldest': ['created_at'],
        'lapsed': [F('cust_last_purchase').asc(nulls_last=True), '-created_at'],
        'most': ['-cust_purchase_count', '-created_at'],
    }.get(sort) or ['-created_at']
    qs = qs.order_by(*ordering)

    page = Paginator(qs, 50).get_page(params.get('page'))
    rows = list(page.object_list)
    for sub in rows:
        sub.last_ago = insights.ago(sub.cust_last_purchase, now)
        sub.recency_key, sub.recency_label = insights.recency(sub.cust_last_purchase, now)

    from urllib.parse import urlencode
    keep = {k: v for k, v in {'q': q, 'status': status, 'package': package, 'last': last, 'sort': sort}.items()
            if v and not (k == 'sort' and v == 'newest')}

    is_main_admin = _is_main_admin(request.user)
    return render(request, 'core/subscriptions.html', {
        'subscriptions': rows,
        'page_obj': page,
        'base_qs': urlencode(keep),
        'q': q, 'sel_status': status, 'sel_package': package, 'sel_last': last, 'sel_sort': sort,
        'status_choices': Subscription.Status.choices,
        'packages': InternetPackage.objects.order_by('display_order', 'name'),
        'filters_active': bool(keep),
        'recent_days': insights.RECENT_DAYS, 'lapsed_days': insights.LAPSED_DAYS,
        'is_main_admin': is_main_admin,
        # Shareholders see their daily quota; the Main Admin has none.
        'quota': None if is_main_admin else ta.allowance(request.user),
        'today': timezone.localdate(),
        'max_minutes': ta.MAX_MINUTES_PER_ADJUSTMENT,
    })


@login_required(login_url='core:admin_login')
def customer_list(request):
    """
    The lists behind the dashboard tiles. ``?filter=`` picks one of the named
    groups in apps/core/customer_insights.py (online, active users, expired,
    lapsed ...), ``?q=`` searches name / phone / email, ``?sort=`` orders by
    purchase recency, number of purchases or spend.
    """
    from urllib.parse import urlencode
    from django.core.paginator import Paginator
    from django.db.models import Q
    from apps.core import customer_insights as insights

    if _staff_role_name(request.user) is None:
        return HttpResponse('Forbidden.', status=403)

    now = timezone.now()
    key = request.GET.get('filter') or 'all'
    if key not in insights.FILTERS:
        key = 'all'
    sort = request.GET.get('sort') or 'recent'
    if sort not in insights.SORTS:
        sort = 'recent'
    q = (request.GET.get('q') or '').strip()

    qs = insights.customers(key, now)
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) | Q(phone_number__icontains=q)
            | Q(email__icontains=q) | Q(username__icontains=q)
        )
    qs = qs.order_by(*insights.SORTS[sort][1])

    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    rows = list(page.object_list)
    for c in rows:
        c.last_ago = insights.ago(c.last_purchase, now)
        c.recency_key, c.recency_label = insights.recency(c.last_purchase, now)
        c.expiry_ago = insights.ago(c.package_expiry, now) if c.package_expiry else ''
        c.expiry_passed = bool(c.package_expiry and c.package_expiry < now)

    def href(**over):
        data = {'filter': key, 'sort': sort, 'q': q}
        data.update(over)
        data = {k: v for k, v in data.items() if v and not (k == 'sort' and v == 'recent') and not (k == 'filter' and v == 'all')}
        return f"{reverse('core:customer_list')}{'?' + urlencode(data) if data else ''}"

    chips = [{
        'key': k, 'label': meta[0], 'icon': meta[2], 'active': k == key,
        'count': insights.count(k, now), 'href': href(filter=k, page=''),
    } for k, meta in insights.FILTERS.items()]

    return render(request, 'core/customers.html', {
        'customers': rows, 'page_obj': page, 'chips': chips,
        'current': {'key': key, 'label': insights.FILTERS[key][0], 'meaning': insights.FILTERS[key][1],
                    'icon': insights.FILTERS[key][2]},
        'q': q, 'sort': sort, 'sorts': [(k, v[0]) for k, v in insights.SORTS.items()],
        'base_qs': urlencode({k: v for k, v in {'filter': key, 'sort': sort, 'q': q}.items() if v}),
        'total_found': page.paginator.count,
        'recent_days': insights.RECENT_DAYS, 'lapsed_days': insights.LAPSED_DAYS,
    })


@login_required(login_url='core:admin_login')
def voucher_management(request):
    """
    In-portal voucher management (no more django-admin redirect).

    Creation is restricted to Role.SHAREHOLDER, who can only ever
    REQUEST a batch  codes are not generated until the Company
    approves it (see approve_voucher_batch). The Company can also
    issue a batch directly here, which is auto-approved since they are
    the approver.
    """
    from apps.packages.models import InternetPackage
    from apps.vouchers.models import Voucher, VoucherBatch

    role = _staff_role_name(request.user)
    is_main_admin = role == 'SUPER_ADMIN'
    can_request = role == 'SHAREHOLDER' or is_main_admin

    new_code = None
    error = None

    if request.method == 'POST':
        if not can_request:
            error = 'Only shareholders (subject to Company approval) or the Company can request vouchers.'
        else:
            package = InternetPackage.objects.filter(id=request.POST.get('package_id')).first()
            try:
                quantity = int(request.POST.get('quantity', '1'))
            except (TypeError, ValueError):
                quantity = 0
            if not package:
                error = 'Choose a package.'
            elif quantity < 1:
                error = 'Enter a quantity of at least 1.'
            else:
                expiry_days = request.POST.get('expiry_days')
                expiry_date = timezone.now() + timezone.timedelta(days=int(expiry_days)) if expiry_days else None
                batch = VoucherBatch.objects.create(
                    name=f'{"Admin-issued" if is_main_admin else "Shareholder request"} {timezone.now():%d %b %Y %H:%M}',
                    package=package, quantity=quantity, expiry_date=expiry_date, created_by=request.user,
                )
                if is_main_admin:
                    batch.approve(request.user)
                    new_code = ', '.join(v.code for v in batch.vouchers.all())
                else:
                    messages.success(request, f'Voucher request for {quantity} x {package.name} submitted  awaiting Company approval.')

    context = {
        'packages': InternetPackage.objects.filter(is_active=True).order_by('display_order'),
        'vouchers': Voucher.objects.select_related('package', 'customer').order_by('-created_at')[:50],
        'new_code': new_code, 'error': error,
        'can_request': can_request,
        'is_main_admin': is_main_admin,
    }
    # Pending shareholder voucher requests moved to their own Main-Admin-only
    # sidebar page (core:voucher_approvals_admin)  no longer shown here.
    context['recent_batches'] = VoucherBatch.objects.select_related(
        'package', 'created_by', 'approved_by'
    ).order_by('-created_at')[:20]

    return render(request, 'core/vouchers.html', context)


@login_required(login_url='core:admin_login')
@require_POST
def approve_voucher_batch(request, batch_id):
    """Main-Admin-only approve/reject action for a shareholder's voucher request."""
    from apps.vouchers.models import VoucherBatch

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    batch = get_object_or_404(VoucherBatch, pk=batch_id, approval_status=VoucherBatch.ApprovalStatus.PENDING)
    action = request.POST.get('action')

    if action == 'approve':
        batch.approve(request.user)
        from apps.core.emails import send_voucher_batch_approved_email
        send_voucher_batch_approved_email(batch)
        messages.success(request, f'Approved: {batch.quantity} voucher(s) generated for {batch.name}.')
    elif action == 'reject':
        reason = request.POST.get('reason', '').strip()
        batch.reject(request.user, reason=reason)
        messages.success(request, f'Rejected {batch.name}.')
    else:
        messages.error(request, 'Unknown action.')

    return redirect('core:voucher_approvals_admin')


@login_required(login_url='core:admin_login')
def withdrawal_requests_admin(request):
    """
    Main-Admin/superuser-only page. Moved out of the Revenue dashboard so
    that page stays about revenue figures, not decisions  this is now a
    standalone sidebar item, only visible to Role.SUPER_ADMIN (see
    admin_base.html), matching every other action page below.
    """
    from apps.billing.models import WithdrawalRequest

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    context = {
        'pending_withdrawals': WithdrawalRequest.objects.filter(
            status=WithdrawalRequest.Status.PENDING
        ).select_related('shareholder', 'shareholder__user').order_by('created_at'),
        'recent_withdrawal_decisions': WithdrawalRequest.objects.exclude(
            status=WithdrawalRequest.Status.PENDING
        ).select_related('shareholder', 'decided_by').order_by('-decided_at')[:20],
    }
    return render(request, 'core/withdrawal_requests.html', context)


@login_required(login_url='core:admin_login')
def voucher_approvals_admin(request):
    """
    Main-Admin/superuser-only page. Moved out of Vouchers for the same
    reason as withdrawal_requests_admin above  a standalone sidebar item.
    """
    from apps.vouchers.models import VoucherBatch

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    context = {
        'pending_batches': VoucherBatch.objects.filter(
            approval_status=VoucherBatch.ApprovalStatus.PENDING
        ).select_related('package', 'created_by').order_by('-created_at'),
    }
    return render(request, 'core/voucher_approvals.html', context)


@login_required(login_url='core:admin_login')
def compose_email(request):
    """
    Main-Admin-only "Send Email" page: a free-form, customizable email the
    Company can send to any chosen set of shareholders  for meeting
    notices, announcements, anything that isn't one of the automatic
    emails (welcome/withdrawal/voucher/share-increase) already sent
    elsewhere in this module.
    """
    from apps.billing.models import Shareholder
    from apps.core.emails import send_custom_email

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    shareholders = Shareholder.objects.filter(is_active=True).select_related('user').order_by('full_name')

    if request.method == 'POST':
        subject = (request.POST.get('subject') or '').strip()
        message = (request.POST.get('message') or '').strip()
        send_to_all = request.POST.get('send_to_all') == 'on'
        selected_ids = request.POST.getlist('shareholder_ids')

        if send_to_all:
            recipients = [sh.user.email for sh in shareholders]
        else:
            recipients = [
                sh.user.email for sh in shareholders if str(sh.id) in selected_ids
            ]

        if not subject or not message:
            messages.error(request, 'Enter both a subject and a message.')
        elif not recipients:
            messages.error(request, 'Choose at least one shareholder to email (or tick "send to all").')
        else:
            send_custom_email(subject, message, recipients, sender_name='the Company')
            messages.success(request, f'Email sent to {len(recipients)} shareholder(s).')
            return redirect('core:compose_email')

    return render(request, 'core/compose_email.html', {'shareholders': shareholders})


@login_required(login_url='core:admin_login')
def shareholder_activity(request):
    """
    Main-Admin-only: tracks when each shareholder last logged in, plus a
    recent login history (both taken from AuditLog's PORTAL_LOGIN
    entries written by shareholder_login  see that view).
    """
    from apps.billing.models import Shareholder
    from apps.core.models import AuditLog

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    shareholders = Shareholder.objects.select_related('user').order_by('-user__last_login')
    login_history = AuditLog.objects.filter(action='PORTAL_LOGIN').select_related('actor').order_by('-created_at')[:100]

    return render(request, 'core/shareholder_activity.html', {
        'shareholders': shareholders,
        'login_history': login_history,
    })


@login_required(login_url='core:admin_login')
def time_adjustments_log(request):
    """
    Visible to every logged-in staff account (Company, shareholders,
    and operational staff alike): every time someone added time to a
    customer's subscription, who did it, which customer it was for, and
    why (add_subscription_time already writes each of these to AuditLog
    this just displays them).

    Older rows were written before the customer was stored on the log
    entry, so for those the customer is looked up from the subscription.
    """
    from apps.billing.models import Subscription
    from apps.core.models import AuditLog

    logs = list(
        AuditLog.objects.filter(action='ADMIN_TIME_ADJUSTMENT').select_related('actor').order_by('-created_at')[:200]
    )
    sub_ids = [int(log.object_id) for log in logs if (log.object_id or '').isdigit()]
    subs = {
        sub.id: sub
        for sub in Subscription.objects.filter(id__in=sub_ids).select_related('customer')
    }

    rows = []
    for log in logs:
        details = log.new_value or {}
        name = details.get('customer_name')
        phone = details.get('customer_phone')
        if not name:
            sub = subs.get(int(log.object_id)) if (log.object_id or '').isdigit() else None
            if sub is not None:
                name, phone = sub.customer.full_name, sub.customer.phone_number
        rows.append({'log': log, 'customer_name': name or '', 'customer_phone': phone or ''})

    return render(request, 'core/time_adjustments.html', {'rows': rows})


def _parse_local_datetime(date_str, time_str):
    """'2026-10-05' + '14:30' -> aware datetime in the project timezone, or None."""
    import datetime as dt
    try:
        naive = dt.datetime.strptime(f'{(date_str or "").strip()} {(time_str or "").strip()}', '%Y-%m-%d %H:%M')
        return timezone.make_aware(naive)
    except (ValueError, TypeError):
        return None


@login_required(login_url='core:admin_login')
@require_POST
def add_subscription_time(request, subscription_id):
    """
    Staff compensation tool: extend a customer's own real subscription
    without touching the original M-Pesa amount or creating a fake
    payment. Logged via AuditLog for accountability: who added time, for
    which customer, how much, why, and the before/after expiry.

    * Main Admin: picks the new expiry with a date + time selector. No limit.
    * Everyone else (shareholders): adds a fixed number of minutes, at most
      MAX_MINUTES_PER_ADJUSTMENT each, and at most DAILY_LIMIT per day
      (plus anything the Main Admin has granted today). See
      apps/core/time_adjustments.py.
    """
    import datetime as dt
    from django.db import transaction
    from apps.accounts.models import User
    from apps.billing.models import Subscription
    from apps.core import time_adjustments as ta
    from apps.core.models import AuditLog

    if _staff_role_name(request.user) is None:
        return HttpResponse('Forbidden.', status=403)

    is_admin = _is_main_admin(request.user)
    reason = request.POST.get('reason', '').strip()
    back = request.META.get('HTTP_REFERER') or reverse('core:subscriptions')

    if not reason:
        messages.error(request, 'A reason is required for the audit log.')
        return redirect(back)

    with transaction.atomic():
        # Serialise this person's adjustments: two requests arriving together
        # must not both pass the daily-limit check.
        User.objects.select_for_update().get(pk=request.user.pk)

        subscription = get_object_or_404(Subscription.objects.select_related('customer'), pk=subscription_id)
        now = timezone.now()
        previous_expiry = subscription.expiry_time
        base = previous_expiry if (previous_expiry and previous_expiry > now) else now

        if is_admin:
            new_expiry = _parse_local_datetime(request.POST.get('expiry_date'), request.POST.get('expiry_time'))
            if new_expiry is None:
                messages.error(request, 'Pick both a date and a time for the new expiry.')
                return redirect(back)
            if new_expiry <= base:
                messages.error(
                    request,
                    f'The new expiry must be later than {timezone.localtime(base):%d %b %Y, %H:%M}.',
                )
                return redirect(back)
            minutes = int((new_expiry - base).total_seconds() // 60)
            if minutes <= 0:
                messages.error(request, 'Choose a time at least one minute later than the current expiry.')
                return redirect(back)
            remaining_note = ''
        else:
            try:
                minutes = int(request.POST.get('minutes', ''))
            except (TypeError, ValueError):
                minutes = 0
            if minutes <= 0:
                messages.error(request, 'Enter a positive number of minutes to add.')
                return redirect(back)
            if minutes > ta.MAX_MINUTES_PER_ADJUSTMENT:
                messages.error(request, f'You can add at most {ta.MAX_MINUTES_PER_ADJUSTMENT} minutes (1 hour) at a time.')
                return redirect(back)
            quota = ta.allowance(request.user)
            if quota['remaining'] <= 0:
                messages.error(
                    request,
                    f"You have used all {quota['limit']} time adjustments for today. "
                    'Use \u201cRequest more adjustments\u201d to ask the Company for more.',
                )
                return redirect(back)
            new_expiry = base + dt.timedelta(minutes=minutes)
            remaining_note = f" {quota['remaining'] - 1} of {quota['limit']} adjustments left today."

        subscription.expiry_time = new_expiry
        if subscription.status != Subscription.Status.ACTIVE:
            subscription.status = Subscription.Status.ACTIVE
        subscription.save(update_fields=['expiry_time', 'status', 'updated_at'])

        AuditLog.objects.create(
            actor=request.user, action='ADMIN_TIME_ADJUSTMENT',
            object_type='Subscription', object_id=str(subscription.id),
            previous_value={'expiry_time': previous_expiry.isoformat() if previous_expiry else None},
            new_value={
                'expiry_time': subscription.expiry_time.isoformat(),
                'minutes_added': minutes, 'reason': reason,
                'customer_id': subscription.customer_id,
                'customer_name': subscription.customer.full_name,
                'customer_phone': subscription.customer.phone_number,
            },
            ip_address=request.META.get('REMOTE_ADDR'),
        )

    messages.success(
        request,
        f'Added {minutes} min for {subscription.customer.full_name}: '
        f'new expiry {timezone.localtime(subscription.expiry_time):%d %b, %H:%M}.{remaining_note}',
    )
    return redirect(back)


@login_required(login_url='core:admin_login')
@require_POST
def request_more_time_adjustments(request):
    """
    A shareholder who has used today's whole allowance asks the Main Admin
    for more. Only one request can be pending at a time.
    """
    from django.db import transaction
    from apps.accounts.models import User
    from apps.core import time_adjustments as ta
    from apps.core.models import TimeAdjustmentRequest

    if _staff_role_name(request.user) is None:
        return HttpResponse('Forbidden.', status=403)
    back = reverse('core:subscriptions')
    if _is_main_admin(request.user):
        messages.info(request, 'The Company has no daily limit on time adjustments.')
        return redirect(back)

    with transaction.atomic():
        User.objects.select_for_update().get(pk=request.user.pk)
        quota = ta.allowance(request.user)
        if quota['remaining'] > 0:
            messages.error(request, f"You still have {quota['remaining']} adjustment(s) left today.")
        elif quota['pending_request'] is not None:
            messages.info(request, 'Your request is already waiting for the Company.')
        else:
            TimeAdjustmentRequest.objects.create(
                requester=request.user, note=(request.POST.get('note') or '').strip()[:255],
            )
            messages.success(request, 'Request sent. The Company will decide how many extra adjustments to give you.')
    return redirect(back)


@login_required(login_url='core:admin_login')
def time_adjustment_requests_admin(request):
    """Main-Admin-only: shareholders asking for more daily time adjustments."""
    from apps.core import time_adjustments as ta
    from apps.core.models import TimeAdjustmentRequest

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    pending = list(
        TimeAdjustmentRequest.objects.filter(status=TimeAdjustmentRequest.Status.PENDING)
        .select_related('requester').order_by('created_at')
    )
    for item in pending:
        item.used_today = ta.used_today(item.requester)
        item.limit_today = ta.DAILY_LIMIT + ta.extra_granted_today(item.requester)

    recent = (
        TimeAdjustmentRequest.objects.exclude(status=TimeAdjustmentRequest.Status.PENDING)
        .select_related('requester', 'decided_by').order_by('-decided_at')[:20]
    )
    return render(request, 'core/time_adjustment_requests.html', {
        'pending_requests': pending,
        'recent_requests': recent,
        'max_grant': ta.MAX_GRANT_PER_REQUEST,
    })


@login_required(login_url='core:admin_login')
@require_POST
def decide_time_adjustment_request(request, request_id):
    """Main-Admin-only: grant a chosen number of extra adjustments, or decline."""
    from django.db import transaction
    from apps.core import time_adjustments as ta
    from apps.core.audit import log_action
    from apps.core.models import TimeAdjustmentRequest

    if not _is_main_admin(request.user):
        return HttpResponse('Forbidden.', status=403)

    back = reverse('core:time_adjustment_requests_admin')
    action = request.POST.get('action')

    with transaction.atomic():
        item = get_object_or_404(
            TimeAdjustmentRequest.objects.select_for_update(),
            pk=request_id, status=TimeAdjustmentRequest.Status.PENDING,
        )
        if action == 'approve':
            try:
                count = int(request.POST.get('count', ''))
            except (TypeError, ValueError):
                count = 0
            if not 1 <= count <= ta.MAX_GRANT_PER_REQUEST:
                messages.error(request, f'Enter a number of adjustments between 1 and {ta.MAX_GRANT_PER_REQUEST}.')
                return redirect(back)
            item.status = TimeAdjustmentRequest.Status.APPROVED
            item.granted_count = count
            item.valid_on = timezone.localdate()
            messages.success(request, f'Granted {count} extra adjustment(s) to {item.requester} for today.')
        elif action == 'decline':
            item.status = TimeAdjustmentRequest.Status.DECLINED
            messages.success(request, f'Declined the request from {item.requester}.')
        else:
            messages.error(request, 'Unknown action.')
            return redirect(back)

        item.decided_by = request.user
        item.decided_at = timezone.now()
        item.save()
        log_action(request.user, f'TIME_ADJUSTMENT_REQUEST_{item.status}', item,
                   new_value={'requester': str(item.requester), 'granted_count': item.granted_count})

    return redirect(back)


@csrf_exempt
@require_POST
def run_scheduled_tasks(request):
    """
    HTTP-triggerable equivalent of `worker.py`'s loop, for anyone using an
    external scheduler (e.g. cron-job.org) instead of a Railway worker
    service. Protected by INTERNAL_TASK_TOKEN   never call this without
    it, it will happily expire subscriptions and disable customers on
    every hit otherwise.

    Auth: header  Authorization: Bearer <INTERNAL_TASK_TOKEN>
    """
    expected = settings.INTERNAL_TASK_TOKEN
    provided = request.headers.get('Authorization', '')
    if not expected or provided != f'Bearer {expected}':
        return JsonResponse({'detail': 'Unauthorized'}, status=401)

    results = {}
    for name in ('expire_subscriptions', 'sync_mikrotik'):
        buf = io.StringIO()
        try:
            call_command(name, stdout=buf)
            results[name] = {'ok': True, 'output': buf.getvalue().strip()}
        except Exception as exc:  # noqa: BLE001   one command failing shouldn't skip the other
            results[name] = {'ok': False, 'error': str(exc)}

    return JsonResponse({'ran_at': timezone.now().isoformat(), 'results': results})


@require_POST
def submit_feedback(request):
    """
    Public endpoint behind the "Recommendations / Customer Experience"
    box on the captive-portal landing page (customers/landing.html). No
    login required  the same device that isn't authenticated onto wifi
    yet is exactly who's filling this in. Captures the device MAC (same
    HOTSPOT_MAC the buy/reconnect/voucher flows already send) purely so
    staff can cross-reference it against sessions/payments later; it's
    never required.
    """
    from apps.core.models import CustomerFeedback

    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    back = reverse('customers:landing') + '#feedback'

    def fail(msg):
        if ajax:
            return JsonResponse({'success': False, 'error': msg}, status=400)
        messages.error(request, msg)
        return redirect(back)

    phone_number = (request.POST.get('phone_number') or '').strip()
    message_text = (request.POST.get('message') or '').strip()
    name = (request.POST.get('name') or '').strip()
    mac_address = (request.POST.get('mac') or '').strip()

    if not phone_number:
        return fail('Please enter a phone number so we can reach you back.')
    if not message_text:
        return fail('Please write your recommendation or message before sending.')

    CustomerFeedback.objects.create(
        name=name,
        phone_number=phone_number,
        mac_address=mac_address,
        message=message_text,
    )

    if ajax:
        return JsonResponse({'success': True})
    messages.success(request, "Thanks! We've received your message and will get back to you.")
    return redirect(back)


@login_required(login_url='core:shareholder_login')
def feedback_list(request):
    """
    Customer recommendations / experience inquiries, visible to the Main
    Admin and every Shareholder (same audience as the revenue portal  
    see _can_view_revenue_dashboard's docstring for why those two roles
    are grouped together throughout this file).
    """
    from apps.core.models import CustomerFeedback

    if not _can_view_revenue_dashboard(request.user):
        return HttpResponse('Forbidden: customer feedback is restricted to shareholders and the Company.', status=403)

    feedback = CustomerFeedback.objects.all()[:300]
    return render(request, 'core/feedback.html', {
        'feedback': feedback,
        'new_count': CustomerFeedback.objects.filter(is_contacted=False).count(),
    })


@login_required(login_url='core:shareholder_login')
@require_POST
def toggle_feedback_contacted(request, feedback_id):
    """Flip a single feedback row's "contacted" flag. Same audience as feedback_list."""
    from apps.core.models import CustomerFeedback

    if not _can_view_revenue_dashboard(request.user):
        return HttpResponse('Forbidden.', status=403)

    item = get_object_or_404(CustomerFeedback, id=feedback_id)
    item.is_contacted = not item.is_contacted
    item.save(update_fields=['is_contacted'])

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'is_contacted': item.is_contacted})
    return redirect('core:feedback_list')
