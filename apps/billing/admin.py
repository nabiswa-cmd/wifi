from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils.html import format_html

from apps.mikrotik.services import connect_payment_device
from .models import Payment, Subscription


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
            return '—'
        url = (reverse('admin:mikrotik_mikrotikjob_changelist')
               + f'?q={obj.mikrotik_username}')
        return format_html('<a href="{}">View MikroTik jobs for {}</a>', url, obj.mikrotik_username)
    router_jobs_link.short_description = 'Router status'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Search by M-Pesa code, phone, or name to find a payment someone's
    messaged you about, tick it, then run "Reconnect selected" — pushes
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
        sub = getattr(obj, 'subscription', None)
        if sub and not sub.is_currently_entitled():
            return format_html('<span style="color:#f85149">expired</span>')
        session = obj.sessions.filter(status='ACTIVE').order_by('-login_time').first()
        if session:
            return format_html('<span style="color:#3fb950">online \u2022 {}</span>', session.mac_address)
        return format_html('<span style="color:#d29922">not connected</span>')
    device_status.short_description = 'Device'

    @admin.action(description="Reconnect selected customers' devices (uses saved MAC)")
    def reconnect_selected(self, request, queryset):
        done, skipped = 0, []
        for payment in queryset.select_related('customer', 'subscription'):
            if payment.status != Payment.Status.SUCCESS:
                skipped.append(f'#{payment.id}: payment not successful')
                continue
            if not payment.mac_address:
                skipped.append(f'#{payment.id}: no MAC on file — never connected via the hotspot')
                continue
            sub = getattr(payment, 'subscription', None)
            if not sub or not sub.is_currently_entitled():
                skipped.append(f'#{payment.id}: no active/entitled subscription')
                continue
            fake_request = RequestFactory().get('/', {'mac': payment.mac_address})
            warning = connect_payment_device(fake_request, payment)
            (skipped.append(f'#{payment.id}: {warning}') if warning else None)
            done += warning is None
        if done:
            self.message_user(request, f'{done} device(s) re-queued — check MikroTikJob shortly for BYPASS_MAC status.')
        if skipped:
            self.message_user(request, 'Skipped: ' + '; '.join(skipped), level='WARNING')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'package', 'status', 'activation_source', 'activation_time', 'expiry_time')
    list_filter = ('status', 'activation_source')
    search_fields = ('customer__full_name', 'customer__phone_number')