"""
Loyalty points: customers earn points on real M-Pesa payments and trade them
for a prepaid voucher.

Protecting the business (why the numbers can't run away):
  * Points come ONLY from a payment Safaricom confirmed as SUCCESS. Vouchers,
    manual time and staff adjustments never earn points, so redeeming a
    reward can't generate more points (no loop).
  * Each reward has a "rebate": the retail price of what you give away as a
    percentage of the money the customer had to spend to earn those points.
    Rewards above the admin's ceiling (max_rebate_percent) can't be saved and
    are not offered, even if a package price is raised later.
  * The ledger is append-only. The balance is the SUM of it, so it can never
    drift from the history, and a refund simply adds a reversing line.
  * Limits: one unused reward voucher at a time, a monthly redemption cap,
    and points lapse after a period of no earning (lazily, no cron needed).
  * Everything is OFF until the admin switches it on.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class LoyaltyConfig(models.Model):
    """Singleton (pk=1) holding the programme's rules. Edited in Django admin."""
    enabled = models.BooleanField(default=False, help_text='Master switch. Nothing is earned or redeemable while off.')
    ksh_per_point = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('10.00'),
        help_text='Money a customer spends to earn 1 point (KSh 10 = 1 point means KSh 100 earns 10 points).',
    )
    max_rebate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('10.00'),
        help_text='Safety ceiling. A reward worth more than this % of the spend needed to earn it cannot be created or redeemed.',
    )
    expiry_days = models.PositiveIntegerField(
        default=180, help_text='Points lapse after this many days with no new points earned. 0 = never.',
    )
    max_redemptions_per_30_days = models.PositiveSmallIntegerField(default=3)
    voucher_valid_days = models.PositiveSmallIntegerField(
        default=7, help_text='How long a reward voucher can be used before it expires (points are not refunded).',
    )

    class Meta:
        verbose_name = 'Loyalty settings'
        verbose_name_plural = 'Loyalty settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Loyalty settings'


class LoyaltyReward(models.Model):
    package = models.OneToOneField('packages.InternetPackage', on_delete=models.CASCADE, related_name='loyalty_reward')
    points_cost = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'points_cost']

    def rebate_percent(self, config=None):
        """Retail value given away as a % of the spend needed to earn the points. Lower is safer."""
        config = config or LoyaltyConfig.load()
        spend_needed = Decimal(self.points_cost) * config.ksh_per_point
        if spend_needed <= 0:
            return Decimal('999')
        return (self.package.price * 100 / spend_needed).quantize(Decimal('0.01'))

    def is_offerable(self, config=None):
        """Active, its package still sold, and still inside the rebate ceiling at today's prices."""
        config = config or LoyaltyConfig.load()
        return (self.is_active and self.package.is_active
                and self.rebate_percent(config) <= config.max_rebate_percent)

    def clean(self):
        if self.points_cost is None or not self.package_id:
            return
        config = LoyaltyConfig.load()
        rebate = self.rebate_percent(config)
        if rebate > config.max_rebate_percent:
            raise ValidationError(
                f'This reward gives away {rebate}% of what a customer spends to earn it, above your '
                f'{config.max_rebate_percent}% ceiling. Raise the points cost to at least '
                f'{int((self.package.price * 100 / config.max_rebate_percent / config.ksh_per_point).to_integral_value(rounding="ROUND_CEILING"))}.'
            )

    def __str__(self):
        return f'{self.package.name} for {self.points_cost} points'


class PointsLedger(models.Model):
    class Kind(models.TextChoices):
        EARN = 'EARN', 'Earned from payment'
        REDEEM = 'REDEEM', 'Redeemed for voucher'
        REVERSAL = 'REVERSAL', 'Payment refunded'
        EXPIRE = 'EXPIRE', 'Expired'
        ADJUST = 'ADJUST', 'Staff adjustment'

    customer = models.ForeignKey('customers.Customer', on_delete=models.PROTECT, related_name='points_ledger')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    points = models.IntegerField(help_text='Positive adds points, negative removes them.')
    payment = models.ForeignKey('billing.Payment', on_delete=models.PROTECT, null=True, blank=True, related_name='points_entries')
    voucher = models.OneToOneField('vouchers.Voucher', on_delete=models.PROTECT, null=True, blank=True, related_name='points_entry')
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['customer', 'created_at'])]
        constraints = [
            # One payment can earn once and be reversed once, however many times a callback repeats.
            models.UniqueConstraint(fields=['payment', 'kind'], condition=models.Q(payment__isnull=False),
                                    name='unique_points_entry_per_payment_kind'),
            models.CheckConstraint(check=~models.Q(points=0), name='points_entry_not_zero'),
        ]

    def __str__(self):
        return f'{self.customer_id} {self.kind} {self.points:+d}'
