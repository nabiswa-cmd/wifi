"""
MikroTikService: the ONLY place billing/customer code is allowed to touch
MikroTik functionality (Section 13). No view, signal, or model anywhere
else may import librouteros or open a socket directly.

Design:
- `MikroTikBackend` is the interface every real backend implements.
- `NullMikroTikBackend` is what runs today: it performs no network I/O and
  is honest about that (Section 36 — never fake live data).
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
    succeed — billing must never assume Internet was granted just because
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
    whichever device was using this subscription before' lives — shared by
    every way a customer can get connected (M-Pesa reconnect, vouchers,
    and eventually manual/login accounts), so the one-payment-one-device
    rule is enforced identically no matter which door they came through.

    Returns a warning string if the router couldn't be reached (never
    pretends success it can't back up — Section 36), or None if clean.
    """
    from django.utils import timezone
    from .models import InternetSession, MikroTikRouter

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
                           "automatically — it may still show as online until it "
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
            get_mikrotik_service(router).create_user(
                username=subscription.mikrotik_username,
                password=subscription.mikrotik_username,
                profile_name=subscription.package.name,
            )
        except MikroTikConnectionError:
            warning = ("Your account is valid and your time is reserved, but we "
                       "couldn't reach the router to get you online just now. "
                       "Try again in a minute, or contact support.")
    else:
        warning = ("Your account is valid and your time is reserved, but no router "
                   "is configured yet, so we can't get you online automatically.")

    return warning


def get_mikrotik_service(router) -> MikroTikBackend:
    """
    Factory the rest of the app calls. Today this always returns the null
    backend. When Phase 4 lands, this becomes:

        if router.is_active:
            return RouterOSBackend(router)
        return NullMikroTikBackend(router)

    and nothing else in the codebase changes.
    """
    return NullMikroTikBackend(router)
