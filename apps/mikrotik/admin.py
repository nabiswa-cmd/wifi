from django.contrib import admin
from django.utils.html import format_html
from .models import MikroTikRouter, MikroTikProfile, InternetSession
from .services import get_mikrotik_service


@admin.register(MikroTikRouter)
class MikroTikRouterAdmin(admin.ModelAdmin):
    list_display = ('name', 'host', 'api_port', 'is_active', 'status_badge', 'last_checked_at')
    readonly_fields = ('last_connection_status', 'last_checked_at')
    actions = ['test_connection']

    def status_badge(self, obj):
        colors = {
            'CONNECTED': '#3fb950', 'DISCONNECTED': '#8b949e', 'AUTH_FAILED': '#f85149',
            'TIMEOUT': '#d29922', 'ERROR': '#f85149', 'UNKNOWN': '#8b949e',
        }
        color = colors.get(obj.last_connection_status, '#8b949e')
        return format_html('<span style="color:{}">{}</span>', color, obj.get_last_connection_status_display())
    status_badge.short_description = 'Status'

    @admin.action(description='Test connection')
    def test_connection(self, request, queryset):
        from django.utils import timezone
        for router in queryset:
            status = get_mikrotik_service(router).test_connection()
            router.last_connection_status = 'CONNECTED' if status.connected else 'DISCONNECTED'
            router.last_checked_at = timezone.now()
            router.save(update_fields=['last_connection_status', 'last_checked_at'])
        self.message_user(request, 'Connection test complete — see status column.')


@admin.register(MikroTikProfile)
class MikroTikProfileAdmin(admin.ModelAdmin):
    list_display = ('profile_name', 'router', 'rate_limit', 'session_timeout')
    list_filter = ('router',)


@admin.register(InternetSession)
class InternetSessionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'router', 'status', 'login_time', 'logout_time', 'bytes_downloaded')
    list_filter = ('status', 'router')
