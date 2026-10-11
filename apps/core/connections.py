"""
Connect / disconnect a customer from the admin portal.

One place defines "paid but not connected" so the dashboard tile, the badge on
the Connections page and its filter can never disagree.

* Connect    : re-queues the customer's device on the router, using the same
               functions the customer's own "reconnect" button uses
               (connect_payment_device / connect_voucher_device).
* Disconnect : pauses the subscription (status SUSPENDED) and cuts the device
               off the router. Pausing is what stops the customer simply
               reconnecting from their own phone: the portal only lets in a
               subscription that is ACTIVE. The minutes that pass while paused
               are added back to the expiry when staff reconnect, so the
               customer does not lose paid time.

Every action is written to the AuditLog.
"""
import logging
import re
from collections import OrderedDict

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.test import RequestFactory
from django.utils import timezone

logger = logging.getLogger(__name__)

# A customer who paid seconds ago is probably still being connected by the
# portal; don't flag them as "not connected" until this long has passed.
GRACE_SECONDS = 60

MAC_RE = re.compile(r'([0-9A-F]{2}:){5}[0-9A-F]{2}')

PAUSE_ACTION = 'subscription_disconnect'
CONNECT_ACTION = 'subscription_connect'

FILTERS = OrderedDict([
    ('unconnected', 'Paid, not connected'),
    ('online', 'Online'),
    ('active', 'All active'),
    ('paused', 'Paused by staff'),
])


def clean_mac(value):
    mac = (value or '').strip().upper().replace('-', ':')
    return mac if MAC_RE.fullmatch(mac) else ''


# --------------------------------------------------------------------------
# Querysets
# --------------------------------------------------------------------------
def base_queryset():
    from apps.billing.models import Subscription
    from apps.mikrotik.models import InternetSession

    live = InternetSession.objects.filter(
        Q(subscription=OuterRef('pk')) | Q(payment__subscription=OuterRef('pk')),
        status=InternetSession.Status.ACTIVE,
    )
    return Subscription.objects.select_related('customer', 'package', 'payment', 'voucher').annotate(
        is_online=Exists(live),
    )


def subscriptions(key='unconnected', now=None):
    from apps.billing.models import Payment, Subscription

    now = now or timezone.now()
    qs = base_queryset()
    entitled = Q(status=Subscription.Status.ACTIVE, expiry_time__gt=now)

    if key == 'paused':
        return qs.filter(status=Subscription.Status.SUSPENDED).order_by('-updated_at')
    if key == 'online':
        return qs.filter(entitled, is_online=True).order_by('-activation_time')
    if key == 'active':
        return qs.filter(entitled).order_by('-activation_time')

    # unconnected: paid (M-Pesa) or voucher, still has time, no device online
    grace = now - timezone.timedelta(seconds=GRACE_SECONDS)
    paid = Q(payment__status=Payment.Status.SUCCESS) | Q(voucher__isnull=False)
    return qs.filter(entitled, paid, is_online=False, activation_time__lte=grace).order_by('-activation_time')


def count(key, now=None):
    return subscriptions(key, now).count()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def known_mac(sub):
    """Best saved MAC for this subscription's device, or ''."""
    from apps.mikrotik.models import InternetSession

    if sub.payment_id and sub.payment and clean_mac(sub.payment.mac_address):
        return clean_mac(sub.payment.mac_address)
    if clean_mac(sub.mikrotik_username):
        return clean_mac(sub.mikrotik_username)
    last = InternetSession.objects.filter(
        Q(subscription=sub) | Q(payment__subscription=sub)
    ).exclude(mac_address='').order_by('-login_time', '-id').first()
    return clean_mac(last.mac_address) if last else ''


def _last_ip(sub):
    from apps.mikrotik.models import InternetSession
    last = InternetSession.objects.filter(Q(subscription=sub) | Q(payment__subscription=sub)).exclude(
        ip_address=None).order_by('-login_time', '-id').first()
    return last.ip_address if last else None


def _audit(actor, action, sub, previous, new, ip):
    from apps.core.models import AuditLog
    AuditLog.objects.create(
        actor=actor, action=action, object_type='Subscription', object_id=str(sub.pk),
        previous_value=previous, new_value=new, ip_address=ip or None,
    )


def _paused_since(sub):
    """When staff paused this subscription (from the audit log), or None."""
    from django.utils.dateparse import parse_datetime
    from apps.core.models import AuditLog

    entry = AuditLog.objects.filter(
        action=PAUSE_ACTION, object_type='Subscription', object_id=str(sub.pk)
    ).order_by('-created_at').first()
    if entry and entry.new_value and entry.new_value.get('paused_at'):
        return parse_datetime(entry.new_value['paused_at'])
    return entry.created_at if entry else None


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
def connect(sub_id, actor, mac='', ip=None):
    """Returns (ok, message)."""
    from apps.billing.models import Subscription
    from apps.mikrotik.services import connect_payment_device, connect_voucher_device

    now = timezone.now()
    sub = base_queryset().get(pk=sub_id)
    was_paused = sub.status == Subscription.Status.SUSPENDED

    if not was_paused and not sub.is_currently_entitled():
        return False, 'That package has already run out of time, so there is nothing to connect.'
    if sub.status not in (Subscription.Status.ACTIVE, Subscription.Status.SUSPENDED):
        return False, 'That subscription is not active.'
    if not sub.payment_id and not sub.voucher_id:
        return False, 'This time was added by hand with no payment or voucher, so there is no device to reconnect.'

    mac = clean_mac(mac) or known_mac(sub)
    if not mac:
        return False, ('No device is saved for this customer. Type the device MAC address '
                       '(AA:BB:CC:DD:EE:FF) and press Connect again.')

    previous = {'status': sub.status, 'expiry_time': sub.expiry_time.isoformat() if sub.expiry_time else None}

    if was_paused:
        with transaction.atomic():
            locked = Subscription.objects.select_for_update().get(pk=sub.pk)
            since = _paused_since(locked)
            if since and locked.expiry_time and locked.expiry_time > since:
                # Give back the time that ran down while the customer was paused.
                locked.expiry_time = locked.expiry_time + (now - since)
            if locked.expiry_time and locked.expiry_time <= now:
                return False, 'That package had already run out before it was paused.'
            locked.status = Subscription.Status.ACTIVE
            locked.save(update_fields=['status', 'expiry_time', 'updated_at'])
        sub.refresh_from_db()

    request = RequestFactory().get('/', {'mac': mac})
    request.META.pop('REMOTE_ADDR', None)       # never record the staff member's IP as the device IP
    last_ip = _last_ip(sub)
    if last_ip:
        request.META['REMOTE_ADDR'] = last_ip

    if sub.payment_id:
        warning = connect_payment_device(request, sub.payment)
    else:
        warning = connect_voucher_device(request, sub.voucher)

    _audit(actor, CONNECT_ACTION, sub, previous, {
        'status': sub.status, 'expiry_time': sub.expiry_time.isoformat() if sub.expiry_time else None,
        'mac': mac, 'resumed': was_paused, 'warning': warning or '',
    }, ip)
    if warning:
        return False, f'Queued, but: {warning}'
    return True, f'Connect request sent for {sub.customer.full_name}. The device should be online in a few seconds.'


def disconnect(sub_id, actor, reason='', ip=None):
    """Returns (ok, message)."""
    from apps.billing.models import Subscription
    from apps.mikrotik.models import InternetSession, MikroTikRouter
    from apps.mikrotik.services import MikroTikConnectionError, get_mikrotik_service

    now = timezone.now()
    with transaction.atomic():
        try:
            sub = Subscription.objects.select_for_update(of=('self',)).select_related('customer', 'payment').get(pk=sub_id)
        except Subscription.DoesNotExist:
            return False, 'Subscription not found.'
        if sub.status != Subscription.Status.ACTIVE:
            return False, 'Only an active package can be disconnected.'
        previous = {'status': sub.status, 'expiry_time': sub.expiry_time.isoformat() if sub.expiry_time else None}
        sub.status = Subscription.Status.SUSPENDED
        sub.save(update_fields=['status', 'updated_at'])
        _audit(actor, PAUSE_ACTION, sub, previous, {
            'status': sub.status, 'paused_at': now.isoformat(), 'reason': reason or 'No reason given',
        }, ip)

    # Router side. First everything cutoff_all_devices knows about (disables the
    # user, removes the MAC bypass and closes the sessions)...
    sub.cutoff_all_devices()

    # ...then the MACs saved on the payment / subscription, in case the device
    # has bypass access but no open session row.
    macs = {m for m in (clean_mac(sub.payment.mac_address) if sub.payment_id else '',
                        clean_mac(sub.mikrotik_username)) if m}
    macs.update(
        clean_mac(m) for m in InternetSession.objects.filter(
            Q(subscription=sub) | Q(payment__subscription=sub), status=InternetSession.Status.ACTIVE
        ).values_list('mac_address', flat=True)
    )
    warning = ''
    router = MikroTikRouter.objects.filter(is_active=True).first()
    for mac in sorted(m for m in macs if m):
        try:
            service = get_mikrotik_service(router)
            service.unbypass_mac(mac)
            service.disable_user(mac)
            service.disconnect_user(mac)
        except MikroTikConnectionError as exc:
            logger.warning('Disconnect %s: router not reachable for %s: %s', sub.pk, mac, exc)
            warning = ' The router could not be reached, so the device may stay online until it is.'
    InternetSession.objects.filter(
        Q(subscription=sub) | Q(payment__subscription=sub), status=InternetSession.Status.ACTIVE
    ).update(status=InternetSession.Status.CLOSED, logout_time=now)

    return True, f'{sub.customer.full_name} has been disconnected and paused.' + warning
