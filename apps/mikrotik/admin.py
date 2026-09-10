from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from apps.billing.models import Payment, Subscription


class SubscriptionInline(admin.StackedInline):
    """
    Everything you need to answer 'why didn't this customer get online'
    without leaving the Payment page: the subscription it created, and a
    direct link to that subscription's MikroTik jobs (status + the exact
    error the agent reported, if any).
    """
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
    list_display = ('customer', 'package', 'amount', 'status', 'mpesa_receipt_number', 'created_at')
    list_filter = ('status', 'package')
    search_fields = ('phone_number', 'checkout_request_id', 'mpesa_receipt_number', 'customer__full_name')
    readonly_fields = ('checkout_request_id', 'merchant_request_id', 'raw_callback_payload')
    inlines = [SubscriptionInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'package', 'status', 'activation_source', 'activation_time', 'expiry_time')
    list_filter = ('status', 'activation_source')
    search_fields = ('customer__full_name', 'customer__phone_number')



    from django.contrib import admin
from .models import MikroTikRouter, MikroTikProfile, InternetSession, MikroTikJob

admin.site.register(MikroTikRouter)
admin.site.register(MikroTikProfile)
admin.site.register(InternetSession)
admin.site.register(MikroTikJob)
