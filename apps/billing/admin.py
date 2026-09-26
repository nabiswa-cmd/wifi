from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.mikrotik.services import connect_payment_device
from .models import Payment, ShareIncreaseRequest, Shareholder, Subscription, WithdrawalRequest


def _resolve_subscription(payment):
    """
    payment.subscription (the direct OneToOne) only exists for the ONE
    payment that originally created a Subscription row. Under the
    default EXTEND renewal behavior, every later top-up/renewal payment
    for the same customer shares that same Subscription without ever
    getting its own link   so checking the direct FK alone makes every
    renewal payment look like it has no subscription at all, when its
    money in fact correctly extended the real one. This mirrors the
    fallback already used in billing/views.py (payment_status,
    reconnect_by_code).
    """
    return getattr(payment, 'subscription', None) or Subscription.objects.filter(
        customer_id=payment.customer_id,
        status=Subscription.Status.ACTIVE,
        expiry_time__gt=timezone.now(),
    ).order_by('-expiry_time').first() 


class SubscriptionInline(admin.StackedInline):
    model = Subscription
    fk_name = 'payment'
    extra = 0
    readonly_fields = ('customer', 'package', 'status', 'mikrotik_username',
                        'activation_time', 'expiry_time', 'router_jobs_link')
    fields = readonly_fields
    can_delete = False

    def router_jobs_link(self, obj):
        if not obj.mikrotik_username:
            return ' '
        url = (reverse('admin:mikrotik_mikrotikjob_changelist')
               + f'?q={obj.mikrotik_username}')
        return format_html('<a href="{}">View MikroTik jobs for {}</a>', url, obj.mikrotik_username)
    router_jobs_link.short_description = 'Router status'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Search by M-Pesa code, phone, or name to find a payment someone's
    messaged you about, tick it, then run "Reconnect selected"  pushes
    a fresh BYPASS_MAC via connect_payment_device using the MAC already
    on file for THIS payment (payment-scoped, so this can never
    accidentally touch a different payment's device).
    """
    list_display = (
        'customer', 'package', 'amount', 'status', 'mpesa_receipt_number',
        'mac_address', 'device_status', 'created_at',
    )
    list_filter = ('status', 'package')
    search_fields = ('phone_number', 'checkout_request_id', 'mpesa_receipt_number', 'customer__full_name')
    readonly_fields = ('checkout_request_id', 'merchant_request_id', 'raw_callback_payload')
    inlines = [SubscriptionInline]
    actions = ['reconnect_selected']

    def device_status(self, obj):
        sub = _resolve_subscription(obj)
        if sub and not sub.is_currently_entitled():
            return format_html('<span style="color:#f85149">expired</span>')
        session = obj.sessions.filter(status='ACTIVE').order_by('-login_time').first()
        if not session and sub:
            session = sub.sessions.filter(status='ACTIVE').order_by('-login_time').first()
        if session:
            return format_html('<span style="color:#3fb950">online \u2022 {}</span>', session.mac_address)
        return format_html('<span style="color:#d29922">not connected</span>')
    device_status.short_description = 'Device'

    @admin.action(description="Reconnect selected customers' devices (uses saved MAC)")
    def reconnect_selected(self, request, queryset):
        done, skipped = 0, []
        for payment in queryset.select_related('customer'):
            if payment.status != Payment.Status.SUCCESS:
                skipped.append(f'#{payment.id}: payment not successful')
                continue
            if not payment.mac_address:
                skipped.append(f'#{payment.id}: no MAC on file  never connected via the hotspot')
                continue
            sub = _resolve_subscription(payment)
            if not sub or not sub.is_currently_entitled():
                skipped.append(f'#{payment.id}: no active/entitled subscription')
                continue
            fake_request = RequestFactory().get('/', {'mac': payment.mac_address})
            warning = connect_payment_device(fake_request, payment)
            (skipped.append(f'#{payment.id}: {warning}') if warning else None)
            done += warning is None
        if done:
            self.message_user(request, f'{done} device(s) re-queued  check MikroTikJob shortly for BYPASS_MAC status.')
        if skipped:
            self.message_user(request, 'Skipped: ' + '; '.join(skipped), level='WARNING')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'package', 'status', 'activation_source', 'activation_time', 'expiry_time')
    list_filter = ('status', 'activation_source')
    search_fields = ('customer__full_name', 'customer__phone_number')


@admin.register(Shareholder)
class ShareholderAdmin(admin.ModelAdmin):
    """
    Main-Admin-only editing surface for the cap table (Section: Revenue
    Visibility for All Shareholders). The user linked here must have a
    StaffProfile with Role.SHAREHOLDER so they land on the shareholder
    revenue dashboard instead of the operational one on login.
    """
    list_display = ('full_name', 'user', 'share_quantity', 'contribution', 'percentage', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('full_name', 'user__username')
    readonly_fields = ('percentage',)


@admin.register(ShareIncreaseRequest)
class ShareIncreaseRequestAdmin(admin.ModelAdmin):
    """
    Full-visibility fallback for the Company; day to day approval/
    rejection happens on the branded Revenue page instead (see
    apps/core/views.py:decide_share_increase).
    """
    list_display = ('shareholder', 'share_quantity', 'contribution_amount', 'status', 'decided_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('shareholder__full_name',)
    readonly_fields = ('decided_by', 'decided_at')


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    """
    Kept here as a full-visibility fallback for the Company; day to day
    approval/rejection happens on the branded Revenue page instead (see
    apps/core/views.py:decide_withdrawal), which is what shareholders use.
    """
    list_display = ('shareholder', 'amount', 'period_start', 'status', 'payment_phone', 'decided_by', 'created_at')
    list_filter = ('status', 'period_start')
    search_fields = ('shareholder__full_name', 'payment_phone', 'payment_account_name')
    readonly_fields = ('decided_by', 'decided_at')