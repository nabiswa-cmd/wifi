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

from apps.customers.models import Customer
from apps.packages.models import InternetPackage
from .models import Payment, Subscription
from .utils import normalize_phone_number
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
