"""
Customer-facing loyalty pages (phone based, no password; customers here have no login).

Two levels of trust:
  * LOOK UP by phone number: shows the points balance and the rewards only. Nothing that
    identifies the person, no history, and an unknown number looks identical to a number
    with 0 points, so the page can't be used to discover who is a customer.
  * VERIFY with an M-Pesa receipt code from a payment made by that number (proof you are
    the payer): unlocks redeeming and the history. Redeeming creates a voucher worth money,
    so it is never available on the phone number alone.
"""
from urllib.parse import urlencode

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.billing.utils import normalize_phone_number
from apps.customers.models import Customer

from . import services
from .models import LoyaltyConfig, PointsLedger

SESSION_KEY = 'loyalty'


def _throttled(request, bucket, limit, seconds):
    """Best-effort rate limit per client IP (in-memory cache, so per server process)."""
    ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
    key = f'loyalty:{bucket}:{ip}'
    if cache.add(key, 1, seconds):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, seconds)
        return False


def _state(request):
    data = request.session.get(SESSION_KEY) or {}
    customer = Customer.objects.filter(pk=data['cid']).first() if data.get('cid') else None
    return customer, bool(data.get('verified') and customer), data.get('phone', '')


def _hotspot(request):
    src = request.POST if request.method == 'POST' else request.GET
    return {'mac': (src.get('mac') or '')[:17], 'link': (src.get('link') or '')[:300]}


def _home_url(request):
    hs = {k: v for k, v in _hotspot(request).items() if v}
    return reverse('loyalty:home') + (('?' + urlencode(hs)) if hs else '')


def _mask(phone):
    return f'0{phone[3:6]} ••• {phone[-3:]}' if phone and len(phone) == 12 else ''


def _is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


def _panel(request):
    """
    Everything the landing page's "My points" section needs, as JSON-safe data.
    Same trust levels as the standalone page: the balance and rewards come from the phone
    number alone; history and the unused reward voucher need the M-Pesa-code verification.
    """
    config = LoyaltyConfig.load()
    customer, verified, phone = _state(request)
    if not phone:
        return {'enabled': config.enabled, 'has_phone': False, 'verified': False, 'points': 0,
                'rewards': [], 'progress': 0, 'next_reward': None,
                'ksh_per_point': services.plain_decimal(config.ksh_per_point), 'expiry_days': config.expiry_days,
                'includes_past': config.include_past_purchases}
    snap = services.snapshot(customer)
    snap.update(has_phone=True, verified=verified, masked_phone=_mask(phone), phone='0' + phone[3:],
                history=[], voucher=None)
    if verified and customer:
        snap['history'] = [
            {'kind': h.get_kind_display(), 'note': h.note, 'points': h.points, 'date': h.created_at.strftime('%d %b')}
            for h in PointsLedger.objects.filter(customer=customer)[:10]
        ]
        v = services.outstanding_voucher(customer)
        if v:
            snap['voucher'] = {'code': v.code, 'package': v.package.name,
                               'expires': v.expiry_date.strftime('%d %b %Y, %H:%M')}
    return snap


def home(request):
    config = LoyaltyConfig.load()
    customer, verified, phone = _state(request)
    snap = services.snapshot(customer) if (customer or phone) else None
    history = []
    if verified:
        history = PointsLedger.objects.filter(customer=customer)[:10]
    return render(request, 'loyalty/points.html', {
        'config': config, 'has_phone': bool(phone), 'masked_phone': _mask(phone),
        'verified': verified, 'snap': snap, 'history': history,
        'hotspot': _hotspot(request),
        'display_phone': ('0' + phone[3:]) if phone else '',
        'ksh_per_point': config.ksh_per_point.normalize(),
    })


@require_POST
def lookup(request):
    ajax = _is_ajax(request)
    if _throttled(request, 'lookup', 20, 3600):
        if ajax:
            return JsonResponse({'success': False, 'error': 'Too many tries. Please wait a little and try again.'}, status=429)
        return render(request, 'loyalty/points.html', {
            'config': LoyaltyConfig.load(), 'error': 'Too many tries. Please wait a little and try again.',
            'hotspot': _hotspot(request), 'ksh_per_point': LoyaltyConfig.load().ksh_per_point.normalize(),
        }, status=429)
    phone = normalize_phone_number(request.POST.get('phone', ''))
    if not phone:
        if ajax:
            return JsonResponse({'success': False, 'error': 'Enter a valid Safaricom number, e.g. 0712345678.'}, status=400)
        return render(request, 'loyalty/points.html', {
            'config': LoyaltyConfig.load(), 'error': 'Enter a valid Safaricom number, e.g. 0712345678.',
            'hotspot': _hotspot(request), 'ksh_per_point': LoyaltyConfig.load().ksh_per_point.normalize(),
        })
    customer = Customer.objects.filter(phone_number=phone).first()
    request.session[SESSION_KEY] = {'cid': customer.pk if customer else None, 'phone': phone, 'verified': False}
    if ajax:
        return JsonResponse({'success': True, **_panel(request)})
    return redirect(_home_url(request))


@require_POST
def verify(request):
    data = request.session.get(SESSION_KEY) or {}
    phone = data.get('phone')
    if not phone:
        return redirect(_home_url(request))
    if _throttled(request, 'verify', 8, 3600):
        return JsonResponse({'success': False, 'error': 'Too many tries. Please wait a little and try again.'}, status=429)
    customer = services.verify_ownership(phone, request.POST.get('code', ''))
    if not customer:
        return JsonResponse({'success': False,
                             'error': "That code doesn't match a payment from this number. Check the M-Pesa SMS and try again."},
                            status=400)
    request.session[SESSION_KEY] = {'cid': customer.pk, 'phone': phone, 'verified': True}
    return JsonResponse({'success': True})


@require_GET
def live(request):
    """
    Polled by the landing page while the "My points" section is on screen. Uses the session's number.
    Only side effect: if the admin allows past purchases, any not-yet-counted old payment is credited once.
    """
    return JsonResponse(_panel(request))


@require_POST
def redeem_view(request):
    customer, verified, phone = _state(request)
    if not verified:
        return JsonResponse({'success': False, 'error': 'Please confirm it is you with an M-Pesa code first.',
                             'need_verify': True}, status=403)
    if _throttled(request, 'redeem', 10, 3600):
        return JsonResponse({'success': False, 'error': 'Too many tries. Please wait a little.'}, status=429)
    try:
        reward_id = int(request.POST.get('reward_id', ''))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Choose a reward.'}, status=400)
    try:
        voucher = services.redeem(customer, reward_id)
    except services.LoyaltyError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    return JsonResponse({
        'success': True, 'code': voucher.code, 'package': voucher.package.name,
        'expires': voucher.expiry_date.strftime('%d %b %Y, %H:%M'),
        'phone': '0' + phone[3:],
        'points': services.balance(customer),
    })


@require_POST
def forget(request):
    request.session.pop(SESSION_KEY, None)
    if _is_ajax(request):
        return JsonResponse({'success': True})
    return redirect(_home_url(request))
