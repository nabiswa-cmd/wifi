import csv

from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone


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
    Section 20's KPI dashboard, now backed by real queries against billing
    and customer data (Phase 2). Charts are left to Phase 5/reporting —
    the numeric cards are the load-bearing part for day-to-day ops.
    """
    from apps.customers.models import Customer
    from apps.billing.models import Payment, Subscription
    from apps.mikrotik.models import InternetSession

    today = timezone.now().date()

    payments_today = Payment.objects.filter(created_at__date=today)
    revenue_today = payments_today.filter(status=Payment.Status.SUCCESS).aggregate(total=Sum('amount'))['total'] or 0

    context = {
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
        total_revenue=Sum('amount'),
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
