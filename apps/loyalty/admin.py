from django.contrib import admin

from .models import LoyaltyConfig, LoyaltyReward, PointsLedger


@admin.register(LoyaltyConfig)
class LoyaltyConfigAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not LoyaltyConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoyaltyReward)
class LoyaltyRewardAdmin(admin.ModelAdmin):
    list_display = ('package', 'package_price', 'points_cost', 'rebate', 'is_active', 'display_order')
    list_editable = ('is_active', 'display_order')

    @admin.display(description='Package price (KSh)')
    def package_price(self, obj):
        return obj.package.price

    @admin.display(description='Rebate (lower is safer)')
    def rebate(self, obj):
        cfg = LoyaltyConfig.load()
        pct = obj.rebate_percent(cfg)
        flag = '' if pct <= cfg.max_rebate_percent else '  OVER LIMIT - not offered'
        return f'{pct}%{flag}'


@admin.register(PointsLedger)
class PointsLedgerAdmin(admin.ModelAdmin):
    """Read-only history; the ledger is never edited so balances always match it."""
    list_display = ('created_at', 'customer', 'kind', 'points', 'note')
    list_filter = ('kind',)
    search_fields = ('customer__full_name', 'customer__phone_number')
    readonly_fields = [f.name for f in PointsLedger._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
