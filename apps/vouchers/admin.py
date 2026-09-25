from django.contrib import admin
from .models import Voucher, VoucherBatch


@admin.register(VoucherBatch)
class VoucherBatchAdmin(admin.ModelAdmin):
    list_display = ('name', 'package', 'quantity', 'created_by', 'created_at')
    actions = ['generate_vouchers']

    @admin.action(description='Generate vouchers for selected batches')
    def generate_vouchers(self, request, queryset):
        total = 0
        for batch in queryset:
            if not batch.vouchers.exists():
                total += len(batch.generate_vouchers())
        self.message_user(request, f'Generated {total} vouchers.')


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('code', 'package', 'status', 'customer', 'created_at', 'expiry_date')
    list_filter = ('status', 'package')
    search_fields = ('code',)
