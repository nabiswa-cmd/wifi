from django.contrib import admin
from .models import SystemSettings, AuditLog, Notification


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
