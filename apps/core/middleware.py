from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


class IdleSessionTimeoutMiddleware:
    """
    Logs out any staff/shareholder account (User.is_staff_account=True)
    after SHAREHOLDER_IDLE_TIMEOUT_SECONDS of inactivity (default: 1 hour
    = 3600s). Customer/captive-portal sessions are left alone entirely 
    a customer sitting idle on the wifi landing page isn't looking at
    anything sensitive and has its own, separate session lifecycle.

    Deliberately tracks a plain '_last_activity' timestamp inside the
    session itself, rather than reaching for Django's built-in
    SESSION_COOKIE_AGE/SESSION_SAVE_EVERY_REQUEST, so this can be scoped
    to staff accounts only without changing customer session behaviour.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout_seconds = getattr(settings, 'SHAREHOLDER_IDLE_TIMEOUT_SECONDS', 3600)

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if user is not None and user.is_authenticated and getattr(user, 'is_staff_account', False):
            now_ts = timezone.now().timestamp()
            last_activity = request.session.get('_last_activity')

            if last_activity is not None and (now_ts - last_activity) > self.timeout_seconds:
                profile = getattr(user, 'staff_profile', None)
                role_name = profile.role.name if (profile and profile.is_active_staff) else None
                logout(request)
                messages.info(request, 'You were logged out after 1 hour of inactivity. Please sign in again.')

                if role_name == 'SHAREHOLDER' or request.path.startswith('/revenue'):
                    return redirect(f"{reverse('customers:landing')}#shareholder-login")
                return redirect('core:admin_login')

            request.session['_last_activity'] = now_ts

        return self.get_response(request)


class AuditRequestMiddleware:
    """
    Stashes the current request on a thread-local-like attribute so model
    save()/delete() signals elsewhere (billing, customers, mikrotik) can
    cheaply pull the acting user + IP without threading them through every
    function signature. Kept intentionally minimal for Phase 1.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
