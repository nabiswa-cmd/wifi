from django.contrib import admin
from .models import SystemSettings, AuditLog, Notification, CustomerFeedback


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    """Only one row ever exists; this admin just edits it (Section 4/28)."""

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('actor', 'action', 'object_type', 'object_id', 'ip_address', 'created_at')
    list_filter = ('action', 'object_type')
    search_fields = ('action', 'object_type', 'object_id')
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'notification_type', 'channel', 'is_sent', 'is_read', 'created_at')
    list_filter = ('notification_type', 'channel', 'is_sent', 'is_read')


@admin.register(CustomerFeedback)
class CustomerFeedbackAdmin(admin.ModelAdmin):
    """Read-mostly: staff can flag a message as contacted, nothing else changes."""
    list_display = ('name', 'phone_number', 'mac_address', 'is_contacted', 'created_at')
    list_filter = ('is_contacted', 'created_at')
    search_fields = ('name', 'phone_number', 'mac_address', 'message')
    readonly_fields = ('name', 'phone_number', 'mac_address', 'message', 'created_at')

    def has_add_permission(self, request):
        return False
