"""
Core app: cross-cutting concerns used by every other app.

SystemSettings - single-row branding/config table (Sections 4, 28)
AuditLog       - immutable record of admin actions (Section 25)
Notification   - modular notification record (Section 26)
"""
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
