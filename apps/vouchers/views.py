"""
Voucher redemption: another way to get connected besides M-Pesa. Staff
generate codes ahead of time (see VoucherBatch.generate_vouchers in
models.py); a customer redeems one here. Lives in the same bottom section
of the landing page as the M-Pesa reconnect form.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Subscription
from apps.billing.utils import normalize_phone_number
from apps.customers.models import Customer
from apps.mikrotik.services import connect_customer_device
from .models import Voucher


def redeem_voucher(request):
    back = reverse('customers:landing') + '#reconnect'

    if request.method != 'POST':
        return redirect(back)

    raw_code = request.POST.get('voucher_code', '').strip().upper()
    raw_phone = request.POST.get('voucher_phone', '').strip()

    if not raw_code:
        messages.error(request, "Enter your voucher code.")
        return redirect(back)

    phone_number = normalize_phone_number(raw_phone)
    if not phone_number:
        messages.error(request, "Enter a valid Safaricom number, e.g. 0712345678, "
                                 "so we know whose device to connect.")
        return redirect(back)

    voucher = Voucher.objects.filter(code__iexact=raw_code).select_related('package', 'customer').first()
    if not voucher:
        messages.error(request, "We couldn't find a voucher with that code.")
        return redirect(back)

    if voucher.expiry_date and voucher.expiry_date < timezone.now():
        if voucher.status == Voucher.Status.UNUSED:
            voucher.status = Voucher.Status.EXPIRED
            voucher.save(update_fields=['status'])
        messages.error(request, "This voucher has expired.")
        return redirect(back)

    customer, _ = Customer.objects.get_or_create(
        phone_number=phone_number, defaults={'full_name': phone_number}
    )

    if voucher.status == Voucher.Status.UNUSED:
        if voucher.customer_id and voucher.customer_id != customer.id:
            messages.error(request, "This voucher was issued to a different phone number.")
            return redirect(back)
        subscription = Subscription.activate_from_voucher(customer, voucher.package, voucher)
        voucher.status = Voucher.Status.USED
        voucher.customer = customer
        voucher.activation_date = timezone.now()
        voucher.save(update_fields=['status', 'customer', 'activation_date'])
    elif voucher.status == Voucher.Status.USED:
        if voucher.customer_id != customer.id:
            messages.error(request, "This voucher has already been used by someone else.")
            return redirect(back)
        subscription = Subscription.objects.filter(voucher=voucher).order_by('-created_at').first()
        if not subscription or not subscription.is_currently_entitled():
            messages.error(request, "This voucher's time has already run out.")
            return redirect(back)
    else:
        messages.error(request, "This voucher isn't usable anymore.")
        return redirect(back)

    warning = connect_customer_device(request, customer, subscription)
    if warning:
        messages.warning(request, warning)

    messages.success(request, f"Connected — your {subscription.package.name} package is active "
                               f"until {subscription.expiry_time:%d %b, %H:%M}.")
    return redirect(back)