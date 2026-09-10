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

    def create_user(self, username: str, password: str, profile_name: str):
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

    def create_user(self, username: str, password: str, profile_name: str):
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


def connect_customer_device(request, customer, subscription):
    """
    The one place 'get this customer's current device online, and kick off
    whichever device was using this subscription before' lives  shared by
    every way a customer can get connected (M-Pesa reconnect, vouchers,
    and eventually manual/login accounts), so the one-payment-one-device
    rule is enforced identically no matter which door they came through.

    Returns a warning string if the router couldn't be reached (never
    pretends success it can't back up  Section 36), or None if clean.
    """
    from django.utils import timezone
    from .models import InternetSession, MikroTikJob, MikroTikRouter

    if not subscription.mikrotik_username:
        subscription.mikrotik_username = f'sub{subscription.id}'
        subscription.save(update_fields=['mikrotik_username', 'updated_at'])

    mac_address = request.GET.get('mac') or request.POST.get('mac') or ''
    ip_address = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR')
    )
    router = MikroTikRouter.objects.filter(is_active=True).first()
    warning = None

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
            except MikroTikConnectionError:
                warning = ("Your old device couldn't be reached to disconnect it "
                           "automatically  it may still show as online until it "
                           "times out on its own.")
        previous_session.status = InternetSession.Status.CLOSED
        previous_session.logout_time = timezone.now()
        previous_session.save(update_fields=['status', 'logout_time'])

    InternetSession.objects.update_or_create(
        subscription=subscription, mac_address=mac_address,
        defaults={
            'customer': customer,
            'router': router,
            'ip_address': ip_address,
            'status': InternetSession.Status.ACTIVE,
            'login_time': timezone.now(),
            'mikrotik_username': subscription.mikrotik_username,
        },
    )

    if router:
        try:
            job = get_mikrotik_service(router).create_user(
                username=subscription.mikrotik_username,
                password=subscription.mikrotik_username,
                profile_name=subscription.package.name,
            )
            if job:
                import time
                for _ in range(6):  # ~3s total
                    job.refresh_from_db()
                    if job.status != MikroTikJob.Status.PENDING:
                        break
                    time.sleep(0.5)
                if job.status == MikroTikJob.Status.FAILED:
                    warning = (f"We couldn't get you online automatically "
                               f"({job.result_detail or 'router error'}). "
                               f"Try reconnecting to the WiFi in a minute, or contact support.")
                elif job.status == MikroTikJob.Status.PENDING:
                    warning = ("Getting you online  this is taking a little longer than "
                               "usual. You should be connected within a few more seconds.")

            # This is what actually grants access — direct MAC bypass on
            # the router, no browser cooperation needed (unlike the
            # hotspot-user login above, which depends on the phone's
            # browser successfully posting to the router's plain-HTTP
            # login page from our HTTPS site, and mobile browsers often
            # silently block that). The hotspot user above still exists
            # as a record and a session-timeout safety net.
            if mac_address:
                get_mikrotik_service(router).bypass_mac(
                    mac_address,
                    comment=f'sub{subscription.id} until {subscription.expiry_time}',
                )
            else:
                warning = warning or (
                    "We couldn't identify your device's MAC address, so we "
                    "couldn't connect you automatically  reconnect to the WiFi "
                    "and try again from the page it redirects you to."
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
    can't talk directly, this never opens a socket to RouterOS itself —
    it writes a MikroTikJob row, which the on-site agent (agent/agent.py)
    picks up over HTTPS polling and executes on the LAN.

    Important honesty note (Section 36): a queued job is NOT a confirmed
    result. create_user()/disconnect_user() returning here only means
    "Django has asked for this" — not "the router has done it yet". The
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
            return RouterStatus(connected=False, detail=f'Agent heartbeat is {int(age)}s old — agent may be offline.')
        return RouterStatus(connected=True, detail='Agent checked in recently.')

    def create_user(self, username: str, password: str, profile_name: str):
        return self._enqueue(
            'CREATE_USER',
            {'username': username, 'password': password, 'profile_name': profile_name},
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
    # test_connection()/get_router_status() — callers should check that
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
