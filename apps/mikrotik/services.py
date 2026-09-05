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
