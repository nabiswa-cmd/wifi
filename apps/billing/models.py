

"""
Billing app: Payment + Subscription, the heart of Sections 10-12, 32.

Hard rule encoded here: Subscription.activate() is only ever called from
the M-Pesa callback handler (Phase 3) after Daraja confirms success  never
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
    # Captured from the hotspot's redirect querystring at the moment the
    # customer clicked "Pay" (see initiate_purchase). Carried through to
    # mpesa_callback  which runs minutes later, server-to-server from
    # Safaricom, with no browser/device context of its own  so THIS is
    # the only place that knows which physical device to grant access to.
    mac_address = models.CharField(max_length=17, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Daraja identifiers  CheckoutRequestID is unique so a duplicate
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
            return  # already processed  no-op, not an error
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
    def cutoff_all_devices(self):
        """
        Disconnects every device currently online on this subscription 
        router-side (disable_user + unbypass_mac) and database-side
        (InternetSession closed with a logout_time). This is the ONE place
        that must run any time a subscription stops being usable, for
        ANY reason: natural expiry (expire_subscriptions), or being
        CANCELLED outright by activate_from_payment's IMMEDIATE renewal
        path replacing it with a fresh subscription.
        """
        import logging
        from apps.mikrotik.models import InternetSession
        from apps.mikrotik.services import MikroTikConnectionError, get_mikrotik_service

        logger = logging.getLogger(__name__)
        now = timezone.now()

        sessions = list(
            self.sessions.filter(status=InternetSession.Status.ACTIVE).select_related('router')
        )
        for session in sessions:
            router = session.router
            if router and session.mac_address:
                try:
                    get_mikrotik_service(router).disable_user(session.mac_address)
                    get_mikrotik_service(router).unbypass_mac(session.mac_address)
                except MikroTikConnectionError as exc:
                    logger.warning(
                        'Could not queue disable/unbypass for subscription %s, '
                        'device %s: %s', self.id, session.mac_address, exc,
                    )
            session.status = InternetSession.Status.CLOSED
            session.logout_time = now
            session.save(update_fields=['status', 'logout_time'])
        return sessions
    def is_currently_entitled(self) -> bool:
        """
        The single source of truth for 'does this customer have Internet
        right now', per Section 11  never inferred client-side.
        """
        return (
            self.status == self.Status.ACTIVE
            and self.expiry_time is not None
            and self.expiry_time > timezone.now()
        )

        @classmethod
    def activate_from_payment(cls, customer, package, payment):
        """
        Root-cause fix: every successful payment gets its own, fully
        independent Subscription — never merged into, or cancelling,
        any other. The old EXTEND/QUEUE/IMMEDIATE branching is gone:
        IMMEDIATE used to CANCEL and cut off whatever was already active
        (the exact bug where a KSh 5 top-up killed a still-valid KSh 35
        session); EXTEND merged two different payments' time into one
        shared expiry, making it impossible to tell which payment still
        "owned" a connected device once one of them should have expired.

        Multiple simultaneous ACTIVE subscriptions per customer, each
        with its own device (see connect_payment_device), are now the
        correct, expected state — not an edge case to guard against.

        Called ONLY after Payment.mark_success() — i.e. only from a
        verified Daraja callback (Section 17's idempotency guard already
        lives there, unchanged).
        """
        now = timezone.now()
        duration = package.duration_as_timedelta()

        new_sub = cls.objects.create(
            customer=customer, package=package, payment=payment,
            activation_time=now, expiry_time=now + duration, status=cls.Status.ACTIVE,
        )

        # Customer.current_package/package_expiry are a denormalized
        # display convenience ONLY — nothing anywhere makes an
        # entitlement/expiry decision from them, that's always read from
        # Subscription.expiry_time directly. Safe to just show whichever
        # active subscription runs longest.
        latest = cls.objects.filter(
            customer=customer, status=cls.Status.ACTIVE, expiry_time__gt=now
        ).order_by('-expiry_time').first()
        if latest:
            customer.current_package = latest.package
            customer.package_expiry = latest.expiry_time
            customer.save(update_fields=['current_package', 'package_expiry', 'updated_at'])

        return new_sub
    @classmethod
    def activate_from_voucher(cls, customer, package, voucher):
        """
        Same root-cause fix as activate_from_payment: every redeemed
        voucher gets its own independent Subscription, never merged into
        or cancelling another one.
        """
        now = timezone.now()
        duration = package.duration_as_timedelta()

        new_sub = cls.objects.create(
            customer=customer, package=package, voucher=voucher,
            activation_source=cls.ActivationSource.VOUCHER,
            activation_time=now, expiry_time=now + duration, status=cls.Status.ACTIVE,
        )

        latest = cls.objects.filter(
            customer=customer, status=cls.Status.ACTIVE, expiry_time__gt=now
        ).order_by('-expiry_time').first()
        if latest:
            customer.current_package = latest.package
            customer.package_expiry = latest.expiry_time
            customer.save(update_fields=['current_package', 'package_expiry', 'updated_at'])

        return new_sub
