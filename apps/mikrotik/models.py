"""
MikroTik app: router config, profile mapping, and live session records
(Sections 13-17). No live data is ever faked (Section 36)  if a router
isn't reachable, connection_status just says so.
"""
from django.conf import settings
from django.db import models


class MikroTikRouter(models.Model):
    class ConnectionStatus(models.TextChoices):
        UNKNOWN = 'UNKNOWN', 'Unknown'
        CONNECTED = 'CONNECTED', 'Connected'
        DISCONNECTED = 'DISCONNECTED', 'Disconnected'
        AUTH_FAILED = 'AUTH_FAILED', 'Authentication Failed'
        TIMEOUT = 'TIMEOUT', 'Timeout'
        ERROR = 'ERROR', 'Error'

    name = models.CharField(max_length=100)
    host = models.CharField(max_length=255, help_text='IP address or hostname')
    api_port = models.PositiveIntegerField(default=8728)
    username = models.CharField(max_length=100)
    # Never exposed to any serializer that reaches the frontend (Section 14).
    password = models.CharField(max_length=255)
    use_ssl = models.BooleanField(default=False, help_text='Use the encrypted API port (normally 8729)')
    is_active = models.BooleanField(default=True)

    last_connection_status = models.CharField(
        max_length=20, choices=ConnectionStatus.choices, default=ConnectionStatus.UNKNOWN
    )
    last_checked_at = models.DateTimeField(blank=True, null=True)

    # Pushed by the on-site agent on every heartbeat (Section 17: this is
    # the *live* view, kept separate from InternetSession's billing record).
    # Never written by anything except the agent's heartbeat call.
    cached_active_users = models.JSONField(default=list, blank=True)
    cached_active_sessions = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mikrotik_router'

    def __str__(self):
        return f'{self.name} ({self.host})'


class MikroTikProfile(models.Model):
    """The MikroTik-side HotSpot user profile name a package maps to
    (Section 15)  e.g. Django '5 Hours' -> MikroTik '5-HOUR-5MBPS'."""
    router = models.ForeignKey(MikroTikRouter, on_delete=models.CASCADE, related_name='profiles')
    profile_name = models.CharField(max_length=100, help_text='Exact profile name as configured on the router')
    rate_limit = models.CharField(max_length=50, blank=True, help_text="e.g. '2M/5M'")
    session_timeout = models.CharField(max_length=20, blank=True, help_text="e.g. '01:00:00'")

    class Meta:
        db_table = 'mikrotik_profile'
        constraints = [
            models.UniqueConstraint(fields=['router', 'profile_name'], name='unique_router_profile')
        ]

    def __str__(self):
        return f'{self.profile_name} @ {self.router.name}'


class MikroTikJob(models.Model):
    """
    A queued instruction for the on-site agent to execute against the
    real router over the LAN (see agent/agent.py). Django can never talk
    to the router directly (it's on Vercel, the router is on-site behind
    NAT) — this table is the bridge. RouterOSBackend below only ever
    writes rows here; only the agent ever marks them DONE/FAILED.
    """
    class JobType(models.TextChoices):
        CREATE_USER = 'CREATE_USER', 'Create hotspot user'
        DISCONNECT_USER = 'DISCONNECT_USER', 'Disconnect user'
        DISABLE_USER = 'DISABLE_USER', 'Disable hotspot user'
        DELETE_USER = 'DELETE_USER', 'Delete hotspot user'
        ACTIVATE_USER = 'ACTIVATE_USER', 'Re-enable hotspot user'
        UPDATE_USER = 'UPDATE_USER', 'Update hotspot user fields'
        SET_BANDWIDTH = 'SET_BANDWIDTH', 'Set bandwidth rate limit'
        SET_SESSION_TIMEOUT = 'SET_SESSION_TIMEOUT', 'Set session timeout'
        BYPASS_MAC = 'BYPASS_MAC', 'Grant a device internet without hotspot login'
        UNBYPASS_MAC = 'UNBYPASS_MAC', 'Revoke a bypassed device'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        DONE = 'DONE', 'Done'
        FAILED = 'FAILED', 'Failed'

    router = models.ForeignKey(MikroTikRouter, on_delete=models.CASCADE, related_name='jobs')
    job_type = models.CharField(max_length=20, choices=JobType.choices)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    result_detail = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'mikrotik_job'
        indexes = [models.Index(fields=['router', 'status'])]

    def __str__(self):
        return f'{self.job_type} ({self.status}) @ {self.router.name}'


class InternetSession(models.Model):
    """
    Database record of a session. Deliberately separate from whatever
    MikroTik reports live (Section 17): this table is the historical/billing
    record; `apps.mikrotik.services.MikroTikService.get_active_sessions()`
    is the live view, and the two are shown side by side, never merged into
    fabricated 'live' data.
    """
    customer = models.ForeignKey('customers.Customer', on_delete=models.CASCADE, related_name='sessions')
    subscription = models.ForeignKey(
        'billing.Subscription', on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions'
    )
    router = models.ForeignKey(MikroTikRouter, on_delete=models.SET_NULL, null=True, related_name='sessions')

    mikrotik_username = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    mac_address = models.CharField(max_length=17, blank=True)

    login_time = models.DateTimeField(blank=True, null=True)
    logout_time = models.DateTimeField(blank=True, null=True)
    bytes_uploaded = models.BigIntegerField(default=0)
    bytes_downloaded = models.BigIntegerField(default=0)

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        CLOSED = 'CLOSED', 'Closed'

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = 'mikrotik_internetsession'
        indexes = [models.Index(fields=['status'])]

    def __str__(self):
        return f'{self.customer} session ({self.status})'
