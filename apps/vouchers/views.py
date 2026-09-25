"""
Voucher redemption: another way to get connected besides M-Pesa. Staff
generate codes ahead of time (see admin-portal Vouchers page / Django
admin's VoucherBatch); a customer redeems one here.

AJAX-only, mirroring the M-Pesa reconnect flow's JSON contract exactly  
the landing page shows a loading spinner and polls voucher_status until
the router confirms the bypass, instead of a full-page redirect with no
feedback while the customer waits.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.billing.models import Subscription
from apps.billing.utils import normalize_phone_number
from apps.customers.models import Customer
from apps.mikrotik.services import connect_voucher_device
from .models import Voucher


def redeem_voucher(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=405)

    raw_code = request.POST.get('voucher_code', '').strip().upper()
    raw_phone = request.POST.get('voucher_phone', '').strip()

    if not raw_code:
        return JsonResponse({'success': False, 'error': 'Enter your voucher code.'})

    phone_number = normalize_phone_number(raw_phone)
    if not phone_number:
        return JsonResponse({'success': False, 'error': 'Enter a valid Safaricom number, e.g. 0712345678.'})

    voucher = Voucher.objects.filter(code__iexact=raw_code).select_related('package', 'customer').first()
    if not voucher:
        return JsonResponse({'success': False, 'error': "We couldn't find a voucher with that code."})

    if voucher.expiry_date and voucher.expiry_date < timezone.now():
        if voucher.status == Voucher.Status.UNUSED:
            voucher.status = Voucher.Status.EXPIRED
            voucher.save(update_fields=['status'])
        return JsonResponse({'success': False, 'error': 'This voucher has expired.'})

    customer, _ = Customer.objects.get_or_create(
        phone_number=phone_number, defaults={'full_name': phone_number}
    )

    if voucher.status == Voucher.Status.UNUSED:
        if voucher.customer_id and voucher.customer_id != customer.id:
            return JsonResponse({'success': False, 'error': 'This voucher was issued to a different phone number.'})
        subscription = Subscription.activate_from_voucher(customer, voucher.package, voucher)
        voucher.status = Voucher.Status.USED
        voucher.customer = customer
        voucher.activation_date = timezone.now()
        voucher.save(update_fields=['status', 'customer', 'activation_date'])
    elif voucher.status == Voucher.Status.USED:
        if voucher.customer_id != customer.id:
            return JsonResponse({'success': False, 'error': 'This voucher has already been used by someone else.'})
        subscription = Subscription.objects.filter(voucher=voucher).order_by('-created_at').first()
        if not subscription or not subscription.is_currently_entitled():
            return JsonResponse({'success': False, 'error': "This voucher's time has already run out."})
    else:
        return JsonResponse({'success': False, 'error': "This voucher isn't usable anymore."})

    warning = connect_voucher_device(request, voucher)
    return JsonResponse({'success': True, 'voucher_id': voucher.id, 'warning': warning})


def voucher_status(request, voucher_id):
    """Polled by the landing page after redeem   only trusts a DONE
    BYPASS_MAC job for THIS exact mac, created after this session's
    login_time, same discipline as billing.views.payment_status."""
    from apps.mikrotik.models import InternetSession, MikroTikJob, MikroTikRouter

    voucher = get_object_or_404(Voucher, pk=voucher_id)
    mac_address = (request.GET.get('mac') or '').upper().replace('-', ':')
    router = MikroTikRouter.objects.filter(is_active=True).first()

    connected = False
    if router and mac_address:
        session = InternetSession.objects.filter(
            voucher=voucher, mac_address=mac_address,
        ).order_by('-login_time').first()
        if session and session.login_time:
            connected = MikroTikJob.objects.filter(
                router=router, job_type=MikroTikJob.JobType.BYPASS_MAC,
                payload__mac_address=mac_address,
                status=MikroTikJob.Status.DONE,
                created_at__gte=session.login_time,
            ).exists()

    return JsonResponse({'connected': connected})