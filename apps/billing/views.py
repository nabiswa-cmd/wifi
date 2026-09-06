"""
Customer-facing purchase flow (Section 9/10/11) and the Daraja callback
(Section 10/32).

`initiate_purchase` creates a PENDING Payment, then actually calls Daraja
via `_trigger_stk_push`. It does NOT activate anything itself — only
`mpesa_callback`, triggered by Safaricom's server hitting our callback URL
after the customer enters their PIN, may mark a payment successful and
activate a subscription (Section 10's hard rule).
"""
import datetime
import json
import logging

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django.contrib import messages
from django.urls import reverse

from apps.customers.models import Customer
from apps.packages.models import InternetPackage
from apps.mikrotik.models import InternetSession, MikroTikRouter
from apps.mikrotik.services import get_mikrotik_service, MikroTikConnectionError
from .models import Payment, Subscription
from .utils import normalize_phone_number, extract_mpesa_code
from . import mpesa

logger = logging.getLogger(__name__)


def _trigger_stk_push(payment: Payment):
    """
    Calls Daraja for real. On failure this marks the payment FAILED
    immediately (rather than leaving it PENDING forever) so the customer
    isn't stuck watching a spinner for a request that was never sent.
    """
    try:
        data = mpesa.stk_push(
            phone_number=payment.phone_number,
            amount=payment.amount,
            account_reference=f'NABISWA{payment.id}',
            transaction_desc=f'{payment.package.name} WiFi',
        )
    except mpesa.MpesaError as exc:
        logger.error('STK push failed for payment %s: %s', payment.id, exc)
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=['status', 'updated_at'])
        return

    payment.checkout_request_id = data.get('CheckoutRequestID')
    payment.merchant_request_id = data.get('MerchantRequestID')
    payment.save(update_fields=['checkout_request_id', 'merchant_request_id', 'updated_at'])


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


@require_POST
def initiate_purchase(request, package_id):
    package = get_object_or_404(InternetPackage, pk=package_id, is_active=True)
    raw_phone = request.POST.get('phone_number', '').strip()
    ajax = _is_ajax(request)

    # Accepts 0712345678, 0712 345 678, +254712345678, 254712345678,
    # 712345678, etc. — normalized once here so every downstream table
    # (Customer, Payment) and the Daraja call all use the same
    # 254XXXXXXXXX format regardless of how the customer typed it.
    phone_number = normalize_phone_number(raw_phone)
    if not phone_number:
        error = 'Enter a valid Safaricom number, e.g. 0712345678.'
        if ajax:
            return JsonResponse({'error': error}, status=400)
        return render(request, 'customers/landing.html', {
            'packages': InternetPackage.objects.filter(is_active=True).order_by('display_order'),
            'error': error,
        })

    full_name = request.POST.get('full_name', '').strip() or phone_number

    customer, _ = Customer.objects.get_or_create(
        phone_number=phone_number,
        defaults={'full_name': full_name},
    )

    payment = Payment.objects.create(
        customer=customer, package=package, phone_number=phone_number, amount=package.price,
        status=Payment.Status.PENDING,
    )
    _trigger_stk_push(payment)
    payment.refresh_from_db()

    if ajax:
        if payment.status == Payment.Status.FAILED:
            return JsonResponse({'error': 'Could not reach M-Pesa. Please try again.'}, status=502)
        # The modal on the landing page takes it from here via
        # /billing/payment/<id>/status/ — no redirect, no new page load.
        return JsonResponse({'payment_id': payment.id, 'status': payment.status})

    return redirect('billing:payment_waiting', payment_id=payment.id)


def payment_waiting(request, payment_id):
    """Fallback page for non-JS clients only — the primary flow never
    navigates here (see landing.html's modal)."""
    payment = get_object_or_404(Payment, pk=payment_id)
    return render(request, 'customers/payment_waiting.html', {'payment': payment})


def payment_status(request, payment_id):
    """Polled by the modal's JS (Section 10 — status is always read from
    the backend record, never assumed client-side)."""
    payment = get_object_or_404(Payment, pk=payment_id)
    return JsonResponse({
        'status': payment.status,
        'receipt': payment.mpesa_receipt_number,
    })

def _parse_transaction_date(value):
    """Daraja sends TransactionDate as an int like 20240521123456."""
    try:
        return timezone.make_aware(datetime.datetime.strptime(str(value), '%Y%m%d%H%M%S'))
    except (ValueError, TypeError):
        return timezone.now()
def reconnect_by_code(request):
    """
    Self-service recovery: a customer whose payment succeeded but whose
    device never got connected (or who wants to switch devices) pastes
    their M-Pesa code here instead of paying again.

    This lives as a section at the bottom of the landing page (see
    customers/landing.html#reconnect) rather than its own page — this view
    only ever needs to handle the POST and bounce straight back there with
    a flash message.

    Never re-verifies the payment with Safaricom — it trusts our own
    Payment record, which was itself only ever marked SUCCESS by a real
    Daraja callback (see mpesa_callback below). Quoting a genuine code
    from your own SMS is, by definition, proof you paid.

    Strict one-payment-one-device: reconnecting on a new device
    immediately disconnects whichever device was previously using this
    subscription's session.
    """
    back = reverse('customers:landing') + '#reconnect'

    if request.method != 'POST':
        return redirect(back)

    code = extract_mpesa_code(request.POST.get('code', ''))
    if not code:
        messages.error(request, "That doesn't look like an M-Pesa code — paste the code "
                                 "(e.g. SFH3JT6LKQ) or the whole confirmation message.")
        return redirect(back)

    payment = (
        Payment.objects
        .filter(mpesa_receipt_number__iexact=code, status=Payment.Status.SUCCESS)
        .select_related('customer', 'package', 'subscription')
        .first()
    )
    if not payment or not payment.subscription:
        messages.error(request, "We couldn't find a completed payment with that code. Double-check it and try again.")
        return redirect(back)

    subscription = payment.subscription
    if not subscription.is_currently_entitled():
        messages.error(request, "This code's session has expired — that package's time has run out.")
        return redirect(back)

    if not subscription.mikrotik_username:
        subscription.mikrotik_username = f'sub{subscription.id}'
        subscription.save(update_fields=['mikrotik_username', 'updated_at'])

    mac_address = request.GET.get('mac') or request.POST.get('mac') or ''
    ip_address = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR')
    )
    router = MikroTikRouter.objects.filter(is_active=True).first()

    previous_session = (
        InternetSession.objects
        .filter(subscription=subscription, status=InternetSession.Status.ACTIVE)
        .exclude(mac_address=mac_address)
        .first()
    )
    if previous_session:
        if previous_session.router:
            try:
                get_mikrotik_service(previous_session.router).disconnect_user(
                    previous_session.mikrotik_username
                )
            except MikroTikConnectionError as exc:
                logger.warning('Could not disconnect previous device for subscription %s: %s',
                               subscription.id, exc)
                messages.warning(request, "Your old device couldn't be reached to disconnect it "
                                           "automatically — it may still show as online until it "
                                           "times out on its own.")
        previous_session.status = InternetSession.Status.CLOSED
        previous_session.logout_time = timezone.now()
        previous_session.save(update_fields=['status', 'logout_time'])

    InternetSession.objects.update_or_create(
        subscription=subscription, mac_address=mac_address,
        defaults={
            'customer': payment.customer,
            'router': router,
            'ip_address': ip_address,
            'status': InternetSession.Status.ACTIVE,
            'login_time': timezone.now(),
            'mikrotik_username': subscription.mikrotik_username,
        },
    )

    if router:
        try:
            get_mikrotik_service(router).create_user(
                username=subscription.mikrotik_username,
                password=code,
                profile_name=payment.package.name,
            )
        except MikroTikConnectionError as exc:
            logger.warning('Could not (re)connect device for subscription %s: %s', subscription.id, exc)
            messages.warning(request, "Your payment is valid and your time is reserved, but we "
                                       "couldn't reach the router to get you online just now. "
                                       "Try again in a minute, or contact support.")
    else:
        messages.warning(request, "Your payment is valid and your time is reserved, but no router "
                                   "is configured yet, so we can't get you online automatically.")

    messages.success(request, f"Reconnected — your {payment.package.name} package is active "
                               f"until {subscription.expiry_time:%d %b, %H:%M}.")
    return redirect(back)

@csrf_exempt
@require_POST
def mpesa_callback(request):
    """
    Safaricom posts here once the customer has responded to the STK
    prompt (entered PIN, cancelled, or timed out). Idempotent by
    construction (Section 32): CheckoutRequestID is unique, and an
    already-SUCCESS payment is a no-op on a repeat callback.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid payload'}, status=400)

    stk_callback = body.get('Body', {}).get('stkCallback', {})
    checkout_request_id = stk_callback.get('CheckoutRequestID')
    result_code = stk_callback.get('ResultCode')

    if not checkout_request_id:
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Missing CheckoutRequestID'}, status=400)

    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(checkout_request_id=checkout_request_id)
        except Payment.DoesNotExist:
            logger.warning('Callback for unknown CheckoutRequestID %s', checkout_request_id)
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        if payment.status == Payment.Status.SUCCESS:
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Already processed'})

        if result_code == 0:
            items = {
                i['Name']: i.get('Value')
                for i in stk_callback.get('CallbackMetadata', {}).get('Item', [])
            }
            receipt = items.get('MpesaReceiptNumber', '')
            transaction_time = _parse_transaction_date(items.get('TransactionDate'))
            payment.mark_success(receipt=receipt, transaction_time=transaction_time, raw_payload=body)
            Subscription.activate_from_payment(payment.customer, payment.package, payment)
        else:
            # 1032 = customer cancelled; anything else = failed/timeout.
            payment.status = Payment.Status.CANCELLED if result_code == 1032 else Payment.Status.FAILED
            payment.raw_callback_payload = body
            payment.save(update_fields=['status', 'raw_callback_payload', 'updated_at'])

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
