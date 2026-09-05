from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login

from apps.packages.models import InternetPackage


def landing(request):
    """
    The captive-portal landing page (Section 9): shows active, database-driven
    packages. No prices/durations are ever hard-coded into the template.
    """
    packages = InternetPackage.objects.filter(is_active=True).order_by('display_order')
    return render(request, 'customers/landing.html', {'packages': packages})


def customer_login(request):
    if request.method == 'POST':
        user = authenticate(
            request,
            username=request.POST.get('username'),
            password=request.POST.get('password'),
        )
        if user is not None:
            login(request, user)
            return redirect('customers:dashboard')
        return render(request, 'customers/login.html', {'error': 'Invalid credentials'})
    return render(request, 'customers/login.html')


@login_required(login_url='customers:login')
def customer_dashboard(request):
    """
    Section 27: current package, remaining time, session, devices, payment
    history — all read from the billing models, never inferred client-side.
    """
    customer = getattr(request.user, 'customer_profile', None)
    context = {'customer': customer}
    if customer:
        context['active_subscription'] = customer.subscriptions.filter(status='ACTIVE').order_by('-expiry_time').first()
        context['payments'] = customer.payments.order_by('-created_at')[:10]
        context['devices'] = customer.devices.filter(is_active=True)
        context['active_session'] = customer.sessions.filter(status='ACTIVE').first()
    return render(request, 'customers/dashboard.html', context)


def payment_success(request):
    return render(request, 'customers/payment_result.html', {'success': True})


def payment_failed(request):
    return render(request, 'customers/payment_result.html', {'success': False})
