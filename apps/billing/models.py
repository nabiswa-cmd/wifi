"""
Billing app: Payment + Subscription, the heart of Sections 10-12, 32.

Hard rule encoded here: Subscription.activate() is only ever called from
the M-Pesa callback handler (Phase 3) after Daraja confirms success — never
from the STK-push-initiation view, and never from client-reported status.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        TIMEOUT = 'TIMEOUT', 'Timeout'
        REFUNDED = 'REFUNDED', 'Refunded'

    customer = models.ForeignKey('customers.Customer', on_delete=models.PROTECT, related_name='payments')
    package = models.ForeignKey('packages.InternetPackage', on_delete=models.PROTECT, related_name='payments')

    phone_number = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Daraja identifiers — CheckoutRequestID is unique so a duplicate
    # callback can never create a second Payment/Subscription (Section 32).
    checkout_request_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    merchant_request_id = models.CharField(max_length=64, blank=True, null=True)
    mpesa_receipt_number = models.CharField(max_length=32, blank=True, null=True)
    transaction_timestamp = models.DateTimeField(blank=True, null=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    raw_callback_payload = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_payment'
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f'{self.customer} - KSh {self.amount} ({self.status})'

    def mark_success(self, receipt: str, transaction_time, raw_payload: dict):
        """
        Idempotent by construction: called only from the callback handler,
        guarded there by a select_for_update + status check so two
        simultaneous callbacks for the same CheckoutRequestID can't both
        pass (Section 10/32).
        """
        if self.status == self.Status.SUCCESS:
            return  # already processed — no-op, not an error
        self.status = self.Status.SUCCESS
        self.mpesa_receipt_number = receipt
        self.transaction_timestamp = transaction_time
        self.raw_callback_payload = raw_payload
        self.save(update_fields=['status', 'mpesa_receipt_number', 'transaction_timestamp',
                                  'raw_callback_payload', 'updated_at'])


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class ActivationSource(models.TextChoices):
        MPESA = 'MPESA', 'M-Pesa payment'
        VOUCHER = 'VOUCHER', 'Voucher'
        MANUAL = 'MANUAL', 'Manual (staff)'

    customer = models.ForeignKey('customers.Customer', on_delete=models.PROTECT, related_name='subscriptions')
    package = models.ForeignKey('packages.InternetPackage', on_delete=models.PROTECT, related_name='subscriptions')
    payment = models.OneToOneField(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='subscription'
    )
    voucher = models.ForeignKey(
        'vouchers.Voucher', on_delete=models.SET_NULL, null=True, blank=True, related_name='subscriptions'
    )

    activation_time = models.DateTimeField(blank=True, null=True)
    expiry_time = models.DateTimeField(blank=True, null=True, db_index=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    activation_source = models.CharField(max_length=10, choices=ActivationSource.choices, default=ActivationSource.MPESA)

    mikrotik_username = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_subscription'
        indexes = [models.Index(fields=['status', 'expiry_time'])]

    def __str__(self):
        return f'{self.customer} - {self.package} ({self.status})'

    def is_currently_entitled(self) -> bool:
        """
        The single source of truth for 'does this customer have Internet
        right now', per Section 11 — never inferred client-side.
        """
        return (
            self.status == self.Status.ACTIVE
            and self.expiry_time is not None
            and self.expiry_time > timezone.now()
        )

    @classmethod
    def activate_from_payment(cls, customer, package, payment):
        """
        Implements Section 12's renewal logic. Default behavior (also the
        SystemSettings.renewal_behavior default): EXTEND — if the customer
        already has time remaining, add the new package's duration onto the
        existing expiry rather than discarding it. This is deliberately the
        safest default: it can never lose purchased time.

        Called ONLY after Payment.mark_success() — i.e. only from a verified
        Daraja callback.
        """
        from apps.core.models import SystemSettings

        now = timezone.now()
        behavior = SystemSettings.load().renewal_behavior
        duration = package.duration_as_timedelta()

        existing = cls.objects.filter(
            customer=customer, status=cls.Status.ACTIVE, expiry_time__gt=now
        ).order_by('-expiry_time').first()

        if existing and behavior == 'EXTEND':
            existing.expiry_time = existing.expiry_time + duration
            existing.save(update_fields=['expiry_time', 'updated_at'])
            new_sub = existing
        elif existing and behavior == 'QUEUE':
            new_sub = cls.objects.create(
                customer=customer, package=package, payment=payment,
                status=cls.Status.PENDING,  # becomes ACTIVE when the current one expires (Phase 2 job)
            )
        else:  # IMMEDIATE, or no existing active subscription
            if existing:
                existing.status = cls.Status.CANCELLED
                existing.save(update_fields=['status', 'updated_at'])
            new_sub = cls.objects.create(
                customer=customer, package=package, payment=payment,
                activation_time=now, expiry_time=now + duration, status=cls.Status.ACTIVE,
            )

        # keep Customer's denormalized fields in sync (Section 7)
        customer.current_package = package
        customer.package_expiry = new_sub.expiry_time
        customer.save(update_fields=['current_package', 'package_expiry', 'updated_at'])
        return new_sub

    @classmethod
    def activate_from_voucher(cls, customer, package, voucher):
        """
        Same renewal semantics as activate_from_payment (Section 12), for
        a voucher code instead of an M-Pesa payment — kept as a sibling
        method rather than overloading activate_from_payment's signature.
        """
        from apps.core.models import SystemSettings

        now = timezone.now()
        behavior = SystemSettings.load().renewal_behavior
        duration = package.duration_as_timedelta()

        existing = cls.objects.filter(
            customer=customer, status=cls.Status.ACTIVE, expiry_time__gt=now
        ).order_by('-expiry_time').first()

        if existing and behavior == 'EXTEND':
            existing.expiry_time = existing.expiry_time + duration
            existing.save(update_fields=['expiry_time', 'updated_at'])
            new_sub = existing
        elif existing and behavior == 'QUEUE':
            new_sub = cls.objects.create(
                customer=customer, package=package, voucher=voucher,
                activation_source=cls.ActivationSource.VOUCHER, status=cls.Status.PENDING,
            )
        else:
            if existing:
                existing.status = cls.Status.CANCELLED
                existing.save(update_fields=['status', 'updated_at'])
            new_sub = cls.objects.create(
                customer=customer, package=package, voucher=voucher,
                activation_source=cls.ActivationSource.VOUCHER,
                activation_time=now, expiry_time=now + duration, status=cls.Status.ACTIVE,
            )

        customer.current_package = package
        customer.package_expiry = new_sub.expiry_time
        customer.save(update_fields=['current_package', 'package_expiry', 'updated_at'])
        return new_sub
