

"""
Billing app: Payment + Subscription, the heart of Sections 10-12, 32.

Hard rule encoded here: Subscription.activate() is only ever called from
the M-Pesa callback handler (Phase 3) after Daraja confirms success  never
from the STK-push-initiation view, and never from client-reported status.
"""
from decimal import Decimal

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

# Flat monthly platform/subscription cost deducted from Company Revenue
# before the remainder is split among shareholders (see Shareholder below).
# Kept as a plain constant rather than a settings value since it's a
# business figure, not deployment config  change here if it's ever revised.
SUBSCRIPTION_COST = Decimal('5500.00')

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
        independent Subscription   never merged into, or cancelling,
        any other. The old EXTEND/QUEUE/IMMEDIATE branching is gone:
        IMMEDIATE used to CANCEL and cut off whatever was already active
        (the exact bug where a KSh 5 top-up killed a still-valid KSh 35
        session); EXTEND merged two different payments' time into one
        shared expiry, making it impossible to tell which payment still
        "owned" a connected device once one of them should have expired.

        Multiple simultaneous ACTIVE subscriptions per customer, each
        with its own device (see connect_payment_device), are now the
        correct, expected state   not an edge case to guard against.

        Called ONLY after Payment.mark_success()   i.e. only from a
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
        # display convenience ONLY   nothing anywhere makes an
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


class Shareholder(models.Model):
    """
    One row per shareholder. Deliberately separate from StaffProfile: a
    shareholder's login/role gates WHICH revenue dashboard they land on
    (see apps/core/views.py:revenue_dashboard), while this model holds
    the ownership figures that dashboard must keep private between
    shareholders (share_quantity, contribution, percentage, earnings).

    Only the Company (Role.SUPER_ADMIN) can ever see every row; a
    shareholder's own view of this data is restricted, in the view
    layer, to their own single row plus company-wide totals.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shareholder_profile'
    )
    full_name = models.CharField(max_length=150, blank=True)
    share_quantity = models.PositiveIntegerField(default=0)
    contribution = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    # Auto-derived in save() from contribution / SystemSettings.total_capital
    # x 100  never hand-edited, so it can never drift out of sync with the
    # total capital figure the Company maintains (see revenue_dashboard).
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), editable=False)
    is_active = models.BooleanField(default=True)

    # Default payout details, editable ONLY by the shareholder from their
    # own account page (see core.views.my_account). Auto-filled onto every
    # new withdrawal request so they don't have to retype them each time;
    # left free to override on the request form itself for a one-off send.
    payment_phone = models.CharField(
        max_length=20, blank=True,
        help_text="Default payout phone number, auto-filled onto every new withdrawal request.",
    )
    payment_account_name = models.CharField(
        max_length=150, blank=True,
        help_text="Default payout account name, auto-filled onto every new withdrawal request.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_shareholder'
        ordering = ['-percentage']

    def __str__(self):
        return self.full_name or self.user.get_username()

    def save(self, *args, **kwargs):
        # Local import: core -> billing would otherwise be circular at
        # module load time.
        from apps.core.models import SystemSettings

        total_capital = SystemSettings.load().total_capital
        subscription_cost = SystemSettings.load().subscription_cost
        if total_capital and total_capital > 0:
            self.percentage = (Decimal(self.contribution) / Decimal(total_capital) * Decimal('100')).quantize(Decimal('0.01'))
        else:
            self.percentage = Decimal('0.00')
        super().save(*args, **kwargs)

    def earnings_for(self, distributable_profit) -> Decimal:
        """Individual Shareholder Earnings = Distributable Profit x share %."""
        return (Decimal(distributable_profit) * self.percentage / Decimal('100')).quantize(Decimal('0.01'))

    @staticmethod
    def distributable_profit(company_revenue) -> Decimal:
        """Distributable Profit = Company Revenue - Subscription Cost (never negative)."""
        from apps.core.models import SystemSettings
        subscription_cost = SystemSettings.load().subscription_cost
        profit = Decimal(company_revenue) - Decimal(subscription_cost)
        return profit if profit > 0 else Decimal('0.00')

    def withdrawn_or_pending_for(self, period_start) -> Decimal:
        """
        Sum of this shareholder's own withdrawal requests for a given
        calendar month that already count against their balance  PENDING
        (awaiting a decision) and PAID (already sent) both hold; only
        REJECTED requests free up the amount again.
        """
        total = self.withdrawal_requests.filter(
            period_start=period_start,
            status__in=[WithdrawalRequest.Status.PENDING, WithdrawalRequest.Status.PAID],
        ).aggregate(total=models.Sum('amount'))['total']
        return total or Decimal('0.00')

    def available_to_withdraw(self, distributable_profit, period_start) -> Decimal:
        """Earnings for the period, minus whatever's already requested/paid against it."""
        remaining = self.earnings_for(distributable_profit) - self.withdrawn_or_pending_for(period_start)
        return remaining if remaining > 0 else Decimal('0.00')


class WithdrawalRequest(models.Model):
    """
    A shareholder's request to withdraw their earnings for a given
    calendar month. Payouts are manual for now  the Company sends the
    money himself (he is the one paying them), so approving a request
    goes straight to PAID; there is no separate "approved but not yet
    paid" state to track while that's true.

    `payment_phone` / `payment_account_name` are exactly the details a
    manual M-Pesa send needs today, and are named so they can be handed
    straight to the Safaricom B2C ("saf dev") app later without a data
    migration  at that point approving a request can trigger the payout
    automatically instead of just recording that it happened.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        REJECTED = 'REJECTED', 'Rejected'

    shareholder = models.ForeignKey(Shareholder, on_delete=models.CASCADE, related_name='withdrawal_requests')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    period_start = models.DateField(help_text='First day of the calendar month this withdrawal is drawn against.')

    # Where the money should go  collected fresh on every request so it
    # always reflects where the shareholder wants THIS payout sent.
    payment_phone = models.CharField(max_length=20)
    payment_account_name = models.CharField(max_length=150, blank=True)
    note = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_withdrawalrequest'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.shareholder} - KSh {self.amount} ({self.status})'

    def approve_and_pay(self, by_user):
        """
        Main-Admin-only (enforced in the view). Manual payouts mean the
        Company only clicks this once the money is already sent, so
        this marks PAID directly rather than going through a separate
        "approved" holding state.
        """
        self.status = self.Status.PAID
        self.decided_by = by_user
        self.decided_at = timezone.now()
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])

    def reject(self, by_user, reason=''):
        self.status = self.Status.REJECTED
        self.decided_by = by_user
        self.decided_at = timezone.now()
        self.rejection_reason = reason
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'rejection_reason', 'updated_at'])


class ShareIncreaseRequest(models.Model):
    """
    A shareholder asking to grow their stake by contributing more capital.
    This is the ONLY path by which Shareholder.share_quantity or
    .contribution ever change after the row is first created  both stay
    read-only everywhere a shareholder can reach (their own account page,
    the withdrawal form, the dashboard) precisely because every change to
    them has to pass through here and be decided by the Company first.

    Approving a request grows SystemSettings.total_capital by exactly the
    contribution funding it, so the capital figure and every shareholder's
    percentage stay derived, never hand-edited, in the same way
    Shareholder.save() already keeps percentage in sync with contribution.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    shareholder = models.ForeignKey(Shareholder, on_delete=models.CASCADE, related_name='share_increase_requests')
    share_quantity = models.PositiveIntegerField(help_text='Additional shares being requested.')
    contribution_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Amount being contributed to fund this increase; added to both the shareholder's "
                  "own contribution and the company's total capital once approved.",
    )
    note = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_shareincreaserequest'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.shareholder} +{self.share_quantity} shares ({self.status})'

    def approve(self, by_user):
        """
        Main-Admin-only (enforced in the view). Order matters: total_capital
        is grown FIRST so that when the shareholder row is re-saved next,
        Shareholder.save()'s percentage calculation already divides against
        the new, larger capital figure  then every other shareholder is
        re-saved too so their percentages shrink to match, exactly like
        update_total_capital does when the Company edits the figure by hand.
        """
        from apps.core.models import SystemSettings

        if self.status != self.Status.PENDING:
            return

        self.status = self.Status.APPROVED
        self.decided_by = by_user
        self.decided_at = timezone.now()
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])

        settings_row = SystemSettings.load()
        settings_row.total_capital = Decimal(settings_row.total_capital) + Decimal(self.contribution_amount)
        settings_row.save(update_fields=['total_capital', 'updated_at'])

        sh = self.shareholder
        sh.share_quantity = sh.share_quantity + self.share_quantity
        sh.contribution = Decimal(sh.contribution) + Decimal(self.contribution_amount)
        sh.save(update_fields=['share_quantity', 'contribution', 'percentage', 'updated_at'])

        for other in Shareholder.objects.exclude(pk=sh.pk):
            other.save(update_fields=['percentage', 'updated_at'])

    def reject(self, by_user, reason=''):
        if self.status != self.Status.PENDING:
            return
        self.status = self.Status.REJECTED
        self.decided_by = by_user
        self.decided_at = timezone.now()
        self.rejection_reason = reason
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'rejection_reason', 'updated_at'])
