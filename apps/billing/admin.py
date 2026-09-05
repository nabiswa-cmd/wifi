from django.contrib import admin
from .models import Payment, Subscription


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('customer', 'package', 'amount', 'status', 'mpesa_receipt_number', 'created_at')
    list_filter = ('status', 'package')
    search_fields = ('phone_number', 'checkout_request_id', 'mpesa_receipt_number', 'customer__full_name')
    readonly_fields = ('checkout_request_id', 'merchant_request_id', 'raw_callback_payload')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'package', 'status', 'activation_source', 'activation_time', 'expiry_time')
    list_filter = ('status', 'activation_source')
    search_fields = ('customer__full_name', 'customer__phone_number')
