"""
Core app: cross-cutting concerns used by every other app.

SystemSettings - single-row branding/config table (Sections 4, 28)
AuditLog       - immutable record of admin actions (Section 25)
Notification   - modular notification record (Section 26)
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class SystemSettings(models.Model):
    """
    Deliberately a singleton (id is always 1). Every branding string the
    templates use comes from here, never hard-coded, per Section 4.
    """
    business_name = models.CharField(max_length=100, default='NABISWA WIFI')
    logo = models.ImageField(upload_to='branding/', blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    support_info = models.TextField(blank=True)
    wifi_name = models.CharField(max_length=100, blank=True)
    portal_message = models.CharField(
        max_length=255, blank=True, default='Choose a package to connect'
    )
    currency = models.CharField(max_length=10, default='KES')
    timezone = models.CharField(max_length=64, default='Africa/Nairobi')

    # Total capital/amount the business has spent/invested to date. Set
    # once here (Main Admin only, from the revenue portal or Django
    # admin) so that Shareholder.save() can auto-derive each
    # shareholder's percentage from their contribution instead of it
    # being hand-entered and drifting out of sync.
    total_capital = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    subscription_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('5500.00'))
    # sensible operational defaults referenced elsewhere (Section 28)
    default_device_limit = models.PositiveSmallIntegerField(default=1)
    renewal_behavior = models.CharField(
        max_length=20,
        choices=[
            ('EXTEND', 'Extend current entitlement'),
            ('QUEUE', 'Queue after current entitlement'),
            ('IMMEDIATE', 'Start immediately, replacing current'),
        ],
        default='EXTEND',
        help_text='Default behavior in Section 12 when a customer buys a package while one is still active.',
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_systemsettings'
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton row is never deleted

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.business_name


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    previous_value = models.JSONField(blank=True, null=True)
    new_value = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'core_auditlog'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['action', 'created_at'])]

    def __str__(self):
        return f'{self.actor}: {self.action} @ {self.created_at:%Y-%m-%d %H:%M}'


class CustomerFeedback(models.Model):
    """
    Recommendation / customer-experience inquiry box shown on the captive
    portal landing page, right after the customer connects. Deliberately
    lightweight (no FK to Customer  a lot of customers submitting this
    haven't necessarily bought a package or created an account yet), and
    keyed to the device's MAC address so staff can cross-reference it
    against sessions/payments if the customer doesn't fully identify
    themselves. Visible to the Main Admin and every Shareholder from the
    revenue/admin portal (see core.views.feedback_list).
    """
    name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, db_index=True)
    mac_address = models.CharField(max_length=32, blank=True, db_index=True)
    message = models.TextField()
    is_contacted = models.BooleanField(
        default=False,
        help_text='Ticked once a staff member has reached out to this customer by phone.',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'core_customerfeedback'
        ordering = ['-created_at']
        verbose_name = 'Customer Feedback'
        verbose_name_plural = 'Customer Feedback'

    def __str__(self):
        return f'{self.name or "Anonymous"} ({self.phone_number})   {self.created_at:%d %b %Y}'


class Notification(models.Model):
    """
    Modular by design (Section 26): `channel` decides who eventually
    delivers it (SMS/email/WhatsApp/push workers can all read this same
    table and mark themselves done independently).
    """
    class NotificationType(models.TextChoices):
        PAYMENT_SUCCESS = 'PAYMENT_SUCCESS', 'Payment successful'
        PAYMENT_FAILED = 'PAYMENT_FAILED', 'Payment failed'
        PACKAGE_ACTIVATED = 'PACKAGE_ACTIVATED', 'Package activated'
        PACKAGE_EXPIRING = 'PACKAGE_EXPIRING', 'Package expiring'
        PACKAGE_EXPIRED = 'PACKAGE_EXPIRED', 'Package expired'
        ACCOUNT_SUSPENDED = 'ACCOUNT_SUSPENDED', 'Account suspended'
        SYSTEM_ERROR = 'SYSTEM_ERROR', 'System error'

    class Channel(models.TextChoices):
        IN_APP = 'IN_APP', 'In-app'
        SMS = 'SMS', 'SMS'
        EMAIL = 'EMAIL', 'Email'
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        PUSH = 'PUSH', 'Push'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.IN_APP)
    message = models.TextField()
    is_sent = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'core_notification'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.notification_type} -> {self.recipient}'
