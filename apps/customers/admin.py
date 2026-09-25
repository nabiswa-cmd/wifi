from django.contrib import admin
from .models import Customer, Device


class DeviceInline(admin.TabularInline):
    model = Device
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'phone_number', 'status', 'current_package', 'package_expiry', 'total_spending')
    list_filter = ('status',)
    search_fields = ('full_name', 'phone_number', 'email', 'username')
    inlines = [DeviceInline]
    actions = ['suspend_customers', 'reactivate_customers']

    @admin.action(description='Suspend selected customers')
    def suspend_customers(self, request, queryset):
        queryset.update(status=Customer.Status.SUSPENDED)

    @admin.action(description='Reactivate selected customers')
    def reactivate_customers(self, request, queryset):
        queryset.update(status=Customer.Status.ACTIVE)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('customer', 'mac_address', 'device_type', 'is_active', 'last_seen')
    list_filter = ('device_type', 'is_active')
