import csv
import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
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
        f'new expiry {subscription.expiry_time:%d %b, %H:%M}.',
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
