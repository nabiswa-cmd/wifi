"""
MikroTikService: the ONLY place billing/customer code is allowed to touch
MikroTik functionality (Section 13). No view, signal, or model anywhere
else may import librouteros or open a socket directly.

Design:
- `MikroTikBackend` is the interface every real backend implements.
- `NullMikroTikBackend` is what runs today: it performs no network I/O and
  is honest about that (Section 36  never fake live data).
- A future `RouterOSBackend` (using `librouteros` or the REST API on newer
  RouterOS) implements the same interface and is swapped in via
  `get_mikrotik_service()` without touching billing/customers code.
"""
from dataclasses import dataclass
from typing import Optional


class MikroTikConnectionError(Exception):
    """Raised when a router is unreachable, refuses auth, or times out."""


@dataclass
class RouterStatus:
    connected: bool
    detail: str


class MikroTikBackend:
    """Interface. Every method below is a no-op contract until a real
    backend is wired up in Phase 4."""

    def __init__(self, router):
        self.router = router  # apps.mikrotik.models.MikroTikRouter

    def connect(self):
        raise NotImplementedError

    def test_connection(self) -> RouterStatus:
        raise NotImplementedError

    def create_user(self, username: str, password: str, profile_name: str, mac_address: str = ''):
        raise NotImplementedError

    def update_user(self, username: str, **fields):
        raise NotImplementedError

    def disable_user(self, username: str):
        raise NotImplementedError

    def delete_user(self, username: str):
        raise NotImplementedError

    def activate_user(self, username: str):
        raise NotImplementedError

    def disconnect_user(self, username: str):
        raise NotImplementedError

    def get_active_users(self):
        raise NotImplementedError

    def get_active_sessions(self):
        raise NotImplementedError

    def get_router_status(self) -> RouterStatus:
        raise NotImplementedError

    def get_user_usage(self, username: str):
        raise NotImplementedError

    def set_bandwidth(self, username: str, rate_limit: str):
        raise NotImplementedError

    def set_session_timeout(self, username: str, timeout: str):
        raise NotImplementedError

    def bypass_mac(self, mac_address: str, comment: str = ''):
        raise NotImplementedError

    def unbypass_mac(self, mac_address: str):
        raise NotImplementedError


class NullMikroTikBackend(MikroTikBackend):
    """
    Active backend until a physical router is configured and Phase 4 lands.
    Every method fails loudly and explicitly rather than pretending to
    succeed  billing must never assume Internet was granted just because
    a payment succeeded (Section 10/32).
    """

    def connect(self):
        raise MikroTikConnectionError('MikroTik not connected.')

    def test_connection(self) -> RouterStatus:
        return RouterStatus(connected=False, detail='MikroTik not connected.')

    def create_user(self, username: str, password: str, profile_name: str, mac_address: str = ''):
        raise MikroTikConnectionError('MikroTik not connected.')

    def update_user(self, username: str, **fields):
        raise MikroTikConnectionError('MikroTik not connected.')

    def disable_user(self, username: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def delete_user(self, username: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def activate_user(self, username: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def disconnect_user(self, username: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def get_active_users(self):
        return []

    def get_active_sessions(self):
        return []

    def get_router_status(self) -> RouterStatus:
        return RouterStatus(connected=False, detail='MikroTik not connected.')

    def get_user_usage(self, username: str):
        return None

    def set_bandwidth(self, username: str, rate_limit: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def set_session_timeout(self, username: str, timeout: str):
        raise MikroTikConnectionError('MikroTik not connected.')

    def bypass_mac(self, mac_address: str, comment: str = ''):
        raise MikroTikConnectionError('MikroTik not connected.')

    def unbypass_mac(self, mac_address: str):
        raise MikroTikConnectionError('MikroTik not connected.')


def connect_payment_device(request, payment):
    """
    THE ownership boundary is the Payment, not the Subscription or the
    Customer (Section 9 of the connection-architecture spec). A
    Subscription can be shared by several renewal payments under the
    default EXTEND behavior, so using it for device identity let one
    payment's reconnect silently kick off a DIFFERENT payment's device.
    payment.mac_address is the single source of truth for "which device
    currently owns this specific M-Pesa code"  read before this call to
    get the OLD device, written at the end to record the NEW one.

    Three cases, matched exactly to the spec:
      A) old_mac blank            -> just bypass new_mac
      B) old_mac == new_mac       -> re-assert bypass (idempotent, no unbypass)
      C) old_mac != new_mac       -> unbypass old_mac, bypass new_mac

    Never touches another payment's mac_address or session  the old_mac
    compared here came from THIS payment's own row, so it cannot belong
    to anyone else's payment by construction.

    Returns a warning string if the router couldn't be reached (never
    pretends success it can't back up  Section 5), or None if clean.
    """
    from django.db import transaction
    from django.utils import timezone
    from apps.billing.models import Subscription
    from .models import InternetSession, MikroTikJob, MikroTikRouter

    raw_mac = (request.GET.get('mac') or request.POST.get('mac') or '').upper().replace('-', ':')
    import re
    new_mac = raw_mac if re.fullmatch(r'([0-9A-F]{2}:){5}[0-9A-F]{2}', raw_mac) else ''

    if not new_mac:
        return ("Your account is valid and your time is reserved, but we "
                "couldn't detect your device's MAC address. Please reconnect "
                "to the Wi-Fi hotspot and open the payment page again from there.")

    # select_for_update serializes concurrent requests for THIS payment
    # only (Section 27)  a row-level lock, so a simultaneous reconnect
    # on a DIFFERENT payment is never blocked by this.
    with transaction.atomic():
        payment = type(payment).objects.select_for_update().get(pk=payment.pk)
        old_mac = payment.mac_address

        subscription = getattr(payment, 'subscription', None) or Subscription.objects.filter(
            customer=payment.customer, status=Subscription.Status.ACTIVE, expiry_time__gt=timezone.now(),
        ).order_by('-expiry_time').first()
        if not subscription:
            return "Your account is valid, but no active package was found to attach this device to."

        if subscription.mikrotik_username != new_mac:
            subscription.mikrotik_username = new_mac
            subscription.save(update_fields=['mikrotik_username', 'updated_at'])

        ip_address = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR')
        )
        router = MikroTikRouter.objects.filter(is_active=True).first()
        warning = None

        if old_mac and old_mac != new_mac:
            # Case C: switching devices on THIS payment only. old_mac came
            # from payment.mac_address, so it can never belong to a
            # different payment (Section 9).
            old_session = InternetSession.objects.filter(
                payment=payment, mac_address=old_mac, status=InternetSession.Status.ACTIVE,
            ).first()
            if router:
                service = get_mikrotik_service(router)
                try:
                    service.unbypass_mac(old_mac)
                    service.disconnect_user(old_mac)
                except MikroTikConnectionError:
                    warning = ("Your old device couldn't be reached to disconnect it "
                               "automatically  it may still show as online until it "
                               "times out on its own.")
            if old_session:
                old_session.status = InternetSession.Status.CLOSED
                old_session.logout_time = timezone.now()
                old_session.save(update_fields=['status', 'logout_time'])
        # Case A (old_mac blank) and Case B (old_mac == new_mac) both fall
        # through to here with nothing removed  exactly per spec.

        InternetSession.objects.update_or_create(
            payment=payment, mac_address=new_mac,
            defaults={
                'customer': payment.customer, 'subscription': subscription, 'router': router,
                'ip_address': ip_address, 'status': InternetSession.Status.ACTIVE,
                'login_time': timezone.now(), 'mikrotik_username': new_mac,
            },
        )

        if payment.mac_address != new_mac:
            payment.mac_address = new_mac
            payment.save(update_fields=['mac_address', 'updated_at'])

        if router:
            mikrotik_profile = subscription.package.mikrotik_profile
            if not mikrotik_profile or mikrotik_profile.router_id != router.id:
                warning = (
                    f"Your account is valid and your time is reserved, but the "
                    f"\u201c{subscription.package.name}\u201d package has no MikroTik "
                    f"profile configured for this router. Set Package \u2192 MikroTik "
                    f"Profile in admin, then reconnect."
                )
            else:
                try:
                    job = get_mikrotik_service(router).create_user(
                        username=new_mac, password=new_mac,
                        profile_name=mikrotik_profile.profile_name, mac_address=new_mac,
                    )
                    if job and job.status == MikroTikJob.Status.FAILED:
                        warning = (f"We couldn't get you online automatically "
                                   f"({job.result_detail or 'router error'}). "
                                   f"Try reconnecting to the WiFi in a minute, or contact support.")
                    # BYPASS_MAC is the operation that actually grants internet
                    # (Section 4)  always (re-)asserted, idempotent on the
                    # agent side, this is what makes Case B's "ensure bypass
                    # is present" work with zero extra logic here.
                    get_mikrotik_service(router).bypass_mac(
                        new_mac, comment=f'payment{payment.id} until {subscription.expiry_time}',
                    )
                except MikroTikConnectionError:
                    warning = ("Your account is valid and your time is reserved, but we "
                               "couldn't reach the router to get you online just now. "
                               "Try again in a minute, or contact support.")
        else:
            warning = ("Your account is valid and your time is reserved, but no router "
                       "is configured yet, so we can't get you online automatically.")

        return warning

class RouterOSBackend(MikroTikBackend):
    """
    The real backend. Since Django (Vercel) and the router (on-site LAN)
    can't talk directly, this never opens a socket to RouterOS itself  
    it writes a MikroTikJob row, which the on-site agent (agent/agent.py)
    picks up over HTTPS polling and executes on the LAN.

    Important honesty note (Section 36): a queued job is NOT a confirmed
    result. create_user()/disconnect_user() returning here only means
    "Django has asked for this"   not "the router has done it yet". The
    agent reports back via the /api/mikrotik/jobs/<id>/complete/ endpoint,
    and test_connection() below only trusts a recent agent heartbeat, not
    the existence of a queued job.
    """

    def _enqueue(self, job_type, payload):
        from .models import MikroTikJob
        return MikroTikJob.objects.create(router=self.router, job_type=job_type, payload=payload)

    def connect(self):
        return True  # no live socket to open from here; see class docstring

    def test_connection(self) -> RouterStatus:
        from django.utils import timezone
        if not self.router.last_checked_at:
            return RouterStatus(connected=False, detail='No heartbeat received from the on-site agent yet.')
        age = (timezone.now() - self.router.last_checked_at).total_seconds()
        if age > 60:
            return RouterStatus(connected=False, detail=f'Agent heartbeat is {int(age)}s old   agent may be offline.')
        return RouterStatus(connected=True, detail='Agent checked in recently.')

    def create_user(self, username: str, password: str, profile_name: str, mac_address: str = ''):
        return self._enqueue(
            'CREATE_USER',
            {'username': username, 'password': password, 'profile_name': profile_name, 'mac_address': mac_address},
        )

    def disconnect_user(self, username: str):
        self._enqueue('DISCONNECT_USER', {'username': username})

    def get_router_status(self) -> RouterStatus:
        return self.test_connection()

    def update_user(self, username: str, **fields):
        self._enqueue('UPDATE_USER', {'username': username, 'fields': fields})

    def disable_user(self, username: str):
        self._enqueue('DISABLE_USER', {'username': username})

    def delete_user(self, username: str):
        self._enqueue('DELETE_USER', {'username': username})

    def activate_user(self, username: str):
        self._enqueue('ACTIVATE_USER', {'username': username})

    def set_bandwidth(self, username: str, rate_limit: str):
        self._enqueue('SET_BANDWIDTH', {'username': username, 'rate_limit': rate_limit})

    def set_session_timeout(self, username: str, timeout: str):
        self._enqueue('SET_SESSION_TIMEOUT', {'username': username, 'timeout': timeout})

    def bypass_mac(self, mac_address: str, comment: str = ''):
        """
        Grants a device internet immediately, with no hotspot login form
        involved  the agent adds it to /ip/hotspot/ip-binding as
        'bypassed', so its traffic simply stops being intercepted. This
        is what makes 'pay -> instantly online' possible: no credentials
        to type, no second page, no cross-origin form post to the
        router's (http, not https) login URL from our https site.
        """
        self._enqueue('BYPASS_MAC', {'mac_address': mac_address, 'comment': comment})

    def unbypass_mac(self, mac_address: str):
        self._enqueue('UNBYPASS_MAC', {'mac_address': mac_address})

    # These three are read-only "what does the router say right now"
    # queries. Since Django can't open a live socket to the router (see
    # class docstring), they read the last snapshot the agent pushed on
    # its most recent heartbeat rather than blocking on a queued job.
    # If that snapshot is stale (agent offline), that's visible via
    # test_connection()/get_router_status()   callers should check that
    # too rather than trusting this data blindly (Section 36).
    def get_active_users(self):
        return self.router.cached_active_users or []

    def get_active_sessions(self):
        return self.router.cached_active_sessions or []

    def get_user_usage(self, username: str):
        for session in self.router.cached_active_sessions or []:
            if session.get('username') == username:
                return session
        return None


def get_mikrotik_service(router) -> MikroTikBackend:
    """
    Factory the rest of the app calls. RouterOSBackend enqueues jobs for
    the on-site agent; NullMikroTikBackend is the honest fallback when
    there's no active router configured at all.
    """
    if router and router.is_active:
        return RouterOSBackend(router)
    return NullMikroTikBackend(router)