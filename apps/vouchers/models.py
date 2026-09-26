"""
Vouchers app (Section 19): prepaid codes that bypass M-Pesa entirely.
"""
import secrets
import string

from django.db import models


def generate_code(length=10):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


class VoucherBatch(models.Model):
    class ApprovalStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending approval'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    name = models.CharField(max_length=100)
    package = models.ForeignKey('packages.InternetPackage', on_delete=models.PROTECT, related_name='voucher_batches')
    quantity = models.PositiveIntegerField()
    expiry_date = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, related_name='voucher_batches'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Approval workflow (Revenue Visibility for All Shareholders): a
    # shareholder can REQUEST a batch, but codes are only ever generated
    # once the Company approves it  never at request time.
    approval_status = models.CharField(max_length=10, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    approved_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_voucher_batches'
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'vouchers_voucherbatch'

    def __str__(self):
        return f'{self.name} ({self.quantity} vouchers)'

    def generate_vouchers(self):
        vouchers = [
            Voucher(batch=self, package=self.package, code=generate_code(), expiry_date=self.expiry_date)
            for _ in range(self.quantity)
        ]
        return Voucher.objects.bulk_create(vouchers)

    def approve(self, approved_by):
        """Only call this from the Main-Admin-only approval view. Codes
        don't exist at all until this runs, so a pending/rejected batch
        can never accidentally be redeemed."""
        from django.utils import timezone as _tz
        self.approval_status = self.ApprovalStatus.APPROVED
        self.approved_by = approved_by
        self.approved_at = _tz.now()
        self.save(update_fields=['approval_status', 'approved_by', 'approved_at'])
        return self.generate_vouchers()

    def reject(self, approved_by, reason=''):
        from django.utils import timezone as _tz
        self.approval_status = self.ApprovalStatus.REJECTED
        self.approved_by = approved_by
        self.approved_at = _tz.now()
        self.rejection_reason = reason
        self.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'rejection_reason'])


class Voucher(models.Model):
    class Status(models.TextChoices):
        UNUSED = 'UNUSED', 'Unused'
        USED = 'USED', 'Used'
        EXPIRED = 'EXPIRED', 'Expired'
        DEACTIVATED = 'DEACTIVATED', 'Deactivated'

    batch = models.ForeignKey(VoucherBatch, on_delete=models.CASCADE, related_name='vouchers', null=True, blank=True)
    code = models.CharField(max_length=32, unique=True, db_index=True)
    package = models.ForeignKey('packages.InternetPackage', on_delete=models.PROTECT, related_name='vouchers')
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.UNUSED)
    customer = models.ForeignKey(
        'customers.Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='vouchers'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    activation_date = models.DateTimeField(blank=True, null=True)
    expiry_date = models.DateTimeField(blank=True, null=True)

    # Current device using this voucher   mirrors Payment.mac_address, so
    # connect_voucher_device can apply the exact same one-code-one-device
    # ownership rule vouchers get, entirely independent of the M-Pesa path.
    mac_address = models.CharField(max_length=17, blank=True)

    class Meta:
        db_table = 'vouchers_voucher'

    def __str__(self):
        return self.code
