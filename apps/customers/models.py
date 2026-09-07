"""
Customers app: everything about the end-user buying Wi-Fi (Sections 7, 18).
"""
from django.conf import settings
from django.db import models


class Customer(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        BLOCKED = 'BLOCKED', 'Blocked'
        INACTIVE = 'INACTIVE', 'Inactive'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_profile',
        null=True, blank=True,
        help_text='Optional: a customer can exist purely from a phone number until they register a login.',
    )
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, unique=True, db_index=True)
    email = models.EmailField(blank=True)
    username = models.CharField(max_length=64, unique=True, blank=True, null=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    registration_date = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(blank=True, null=True)

    # Denormalized convenience pointers — the source of truth is billing.Subscription;
    # these two fields are kept in sync by the billing engine so simple list/detail
    # pages don't need a join for the common case.
    current_package = models.ForeignKey(
        'packages.InternetPackage', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    package_expiry = models.DateTimeField(blank=True, null=True, db_index=True)

    total_spending = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    # Populated once MikroTik integration is live (Section 7)
    mikrotik_username = models.CharField(max_length=64, blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    device_info = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customers_customer'
        indexes = [models.Index(fields=['status']), models.Index(fields=['phone_number'])]

    def __str__(self):
        return f'{self.full_name} ({self.phone_number})'


class Device(models.Model):
    class DeviceType(models.TextChoices):
        PHONE = 'PHONE', 'Phone'
        LAPTOP = 'LAPTOP', 'Laptop'
        TABLET = 'TABLET', 'Tablet'
        OTHER = 'OTHER', 'Other'

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='devices')
    mac_address = models.CharField(max_length=17, db_index=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.OTHER)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'customers_device'
        constraints = [
            models.UniqueConstraint(fields=['customer', 'mac_address'], name='unique_customer_device')
        ]

    def __str__(self):
        return f'{self.mac_address} ({self.customer})'
