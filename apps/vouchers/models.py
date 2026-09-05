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
    name = models.CharField(max_length=100)
    package = models.ForeignKey('packages.InternetPackage', on_delete=models.PROTECT, related_name='voucher_batches')
    quantity = models.PositiveIntegerField()
    expiry_date = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, related_name='voucher_batches'
    )
    created_at = models.DateTimeField(auto_now_add=True)

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

    class Meta:
        db_table = 'vouchers_voucher'

    def __str__(self):
        return self.code
