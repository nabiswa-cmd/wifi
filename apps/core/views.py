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
    """Main Admin and Shareholder roles only  see revenue_dashboard's docstring."""
    return user.is_authenticated and _staff_role_name(user) in ('SUPER_ADMIN', 'SHAREHOLDER')


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
    from apps.mikrotik.models import InternetSession

    today = timezone.now().date()

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
        'active_customers': Customer.objects.filter(status=Customer.Status.ACTIVE).count(),
        'expired_customers': Customer.objects.filter(
            package_expiry__lt=timezone.now()
        ).exclude(package_expiry__isnull=True).count(),
        'online_users': InternetSession.objects.filter(status='ACTIVE').count(),
        'todays_revenue': revenue_today,
        'todays_payments': payments_today.count(),
        'successful_payments': payments_today.filter(status=Payment.Status.SUCCESS).count(),
        'failed_payments': payments_today.filter(
            status__in=[Payment.Status.FAILED, Payment.Status.CANCELLED, Payment.Status.TIMEOUT]
        ).count(),
        'pending_payments': payments_today.filter(status=Payment.Status.PENDING).count(),
        'active_packages': Subscription.objects.filter(status=Subscription.Status.ACTIVE).count(),
        'todays_sessions': InternetSession.objects.filter(login_time__date=today).count(),
        'chart_labels': [d.strftime('%d %b') for d in chart_days],
        'chart_revenue': [float(revenue_by_day.get(d, 0)) for d in chart_days],
        'chart_sessions': [sessions_by_day.get(d, 0) for d in chart_days],
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

    Both shareholders AND the Main Admin sign in here to reach
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

    Open to Role.SUPER_ADMIN (Main Admin) and Role.SHAREHOLDER only 
    everyone else gets a 403, matching "Shareholders can see the
    company's overall revenue... The Main Admin can see everything".

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
        return HttpResponse('Forbidden: revenue dashboard is restricted to shareholders and the Main Admin.', status=403)

    is_main_admin = _is_main_admin(request.user)
    now = timezone.now()
    today = now.date()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    revenue_qs = Payment.objects.filter(status=Payment.Status.SUCCESS)

    todays_revenue = revenue_qs.filter(created_at__date=today).aggregate(t=Sum('amount'))['t'] or 0
    todays_payment_count = revenue_qs.filter(created_at__date=today).count()

    month_agg = revenue_qs.filter(created_at__date__gte=month_start, created_at__date__lte=today).aggregate(
        total=Sum('amount'), count=Count('id')
    )
    company_revenue = month_agg['total'] or 0
    month_payment_count = month_agg['count'] or 0

    prev_month_revenue = revenue_qs.filter(
        created_at__date__gte=prev_month_start, created_at__date__lte=prev_month_end
    ).aggregate(t=Sum('amount'))['t'] or 0

    if prev_month_revenue:
        growth_pct = float((company_revenue - prev_month_revenue) / prev_month_revenue * 100)
    else:
        growth_pct = 100.0 if company_revenue else 0.0

    # Daily curve across the current financial month so far.
    days_so_far = [month_start + timedelta(days=i) for i in range((today - month_start).days + 1)]
    revenue_by_day = {
        row['created_at__date']: row['total']
        for row in revenue_qs.filter(created_at__date__gte=month_start, created_at__date__lte=today)
        .values('created_at__date').annotate(total=Sum('amount'))
    }
    daily_chart_labels = [d.strftime('%d %b') for d in days_so_far]
    daily_chart_revenue = [float(revenue_by_day.get(d, 0)) for d in days_so_far]

    # Historical monthly performance (previous financial periods), oldest first.
    historical = (
        revenue_qs.filter(created_at__date__lt=month_start, created_at__gte=now - timedelta(days=365))
        .annotate(month=TruncMonth('created_at'))
        .values('month').annotate(total=Sum('amount')).order_by('month')
    )
    historical_labels = [h['month'].strftime('%b %Y') for h in historical]
    historical_totals = [float(h['total']) for h in historical]

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
        'company_revenue': company_revenue,
        'month_payment_count': month_payment_count,
        'prev_month_revenue': prev_month_revenue,
        'growth_pct': round(growth_pct, 1),
        'distributable_profit': distributable_profit,
        'daily_chart_labels': daily_chart_labels,
        'daily_chart_revenue': daily_chart_revenue,
        'historical_labels': historical_labels,
        'historical_totals': historical_totals,
        'current_month_label': month_start.strftime('%B %Y'),
    }

    from apps.billing.models import WithdrawalRequest

    # My own earnings + withdrawal standing  applies to any logged-in
    # user with a linked Shareholder row, Main Admin included (he "can
    # also be a shareholder"), not just the SHAREHOLDER-role branch below.
    context['my_shareholder'] = my_shareholder
    context['my_earnings'] = my_shareholder.earnings_for(distributable_profit) if my_shareholder else None
    if my_shareholder:
        context['withdrawal_period'] = month_start
        context['available_to_withdraw'] = my_shareholder.available_to_withdraw(distributable_profit, month_start)
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

    return render(request, 'core/revenue_dashboard.html', context)


@login_required(login_url='core:shareholder_login')
@require_POST
def request_withdrawal(request):
    """
    A shareholder (or the Main Admin, if he's also a shareholder) asks to
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

    today = timezone.now().date()
    month_start = today.replace(day=1)
    revenue_qs = Payment.objects.filter(status=Payment.Status.SUCCESS)
    company_revenue = revenue_qs.filter(
        created_at__date__gte=month_start, created_at__date__lte=today
    ).aggregate(total=Sum('amount'))['total'] or 0
    distributable_profit = Shareholder.distributable_profit(company_revenue)
    available = my_shareholder.available_to_withdraw(distributable_profit, month_start)

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
        messages.error(request, f'You can withdraw up to {available} this month.')
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

        messages.success(request, f'Withdrawal request for {amount} submitted  awaiting Main Admin approval.')

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
    This only ever queues a ShareIncreaseRequest for the Main Admin to
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
            'awaiting Main Admin approval.',
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
    if params.get('status'):
        qs = qs.filter(status=params['status'])
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
    return render(request, 'core/payments.html', {'payments': qs[:200], 'totals': totals})


@login_required(login_url='core:admin_login')
def subscription_management(request):
    """Section 22: full subscription/entitlement history, never overwritten."""
    from apps.billing.models import Subscription

    qs = Subscription.objects.select_related('customer', 'package').order_by('-created_at')
    status_param = request.GET.get('status')
    if status_param:
        qs = qs.filter(status=status_param)
    return render(request, 'core/subscriptions.html', {'subscriptions': qs[:200]})
@login_required(login_url='core:admin_login')
def voucher_management(request):
    """
    In-portal voucher management (no more django-admin redirect).

    Creation is restricted to Role.SHAREHOLDER, who can only ever
    REQUEST a batch  codes are not generated until the Main Admin
    approves it (see approve_voucher_batch). The Main Admin can also
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
            error = 'Only shareholders (subject to Main Admin approval) or the Main Admin can request vouchers.'
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
                    messages.success(request, f'Voucher request for {quantity} x {package.name} submitted  awaiting Main Admin approval.')

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
    Main Admin can send to any chosen set of shareholders  for meeting
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
            send_custom_email(subject, message, recipients, sender_name='the Main Admin')
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
    Visible to every logged-in staff account (Main Admin, shareholders,
    and operational staff alike): every time someone added time to a
    customer's subscription, who did it, and why (add_subscription_time
    already writes each of these to AuditLog  this just displays them).
    """
    from apps.core.models import AuditLog

    logs = AuditLog.objects.filter(action='ADMIN_TIME_ADJUSTMENT').select_related('actor').order_by('-created_at')[:200]
    return render(request, 'core/time_adjustments.html', {'logs': logs})


@login_required(login_url='core:admin_login')
@require_POST
def add_subscription_time(request, subscription_id):
    """
    Staff compensation tool: extend a customer's own real subscription
    without touching the original M-Pesa amount or creating a fake
    payment. Logged via AuditLog for accountability   who added time, how
    much, why, and the before/after expiry.
    """
    from apps.billing.models import Subscription
    from apps.core.models import AuditLog

    subscription = get_object_or_404(Subscription, pk=subscription_id)
    try:
        minutes = int(request.POST.get('minutes', ''))
    except (TypeError, ValueError):
        minutes = 0
    reason = request.POST.get('reason', '').strip()
    back = request.META.get('HTTP_REFERER') or reverse('core:subscriptions')

    if minutes <= 0:
        messages.error(request, 'Enter a positive number of minutes to add.')
        return redirect(back)
    if not reason:
        messages.error(request, 'A reason is required for the audit log.')
        return redirect(back)

    previous_expiry = subscription.expiry_time
    base = previous_expiry if (previous_expiry and previous_expiry > timezone.now()) else timezone.now()
    subscription.expiry_time = base + timezone.timedelta(minutes=minutes)
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
        },
        ip_address=request.META.get('REMOTE_ADDR'),
    )

    messages.success(
        request,
        f'Added {minutes} min for {subscription.customer.full_name}   '
        f'new expiry {timezone.localtime(subscription.expiry_time):%d %b, %H:%M}.',
    )
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
        return HttpResponse('Forbidden: customer feedback is restricted to shareholders and the Main Admin.', status=403)

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
