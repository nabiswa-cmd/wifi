from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import MikroTikRouter, MikroTikProfile, MikroTikJob, InternetSession
from .services import get_mikrotik_service


@admin.register(MikroTikRouter)
class MikroTikRouterAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'host', 'api_port', 'is_active', 'status_badge',
        'agent_heartbeat_age', 'active_user_count', 'last_checked_at',
    )
    readonly_fields = (
        'last_connection_status', 'last_checked_at',
        'cached_active_users', 'cached_active_sessions',
    )
    actions = ['test_connection']

    def status_badge(self, obj):
        colors = {
            'CONNECTED': '#3fb950', 'DISCONNECTED': '#8b949e', 'AUTH_FAILED': '#f85149',
            'TIMEOUT': '#d29922', 'ERROR': '#f85149', 'UNKNOWN': '#8b949e',
        }
        color = colors.get(obj.last_connection_status, '#8b949e')
        return format_html('<span style="color:{}">{}</span>', color, obj.get_last_connection_status_display())
    status_badge.short_description = 'Status'

    def agent_heartbeat_age(self, obj):
        if not obj.last_checked_at:
            return format_html('<span style="color:#f85149">never</span>')
        age = int((timezone.now() - obj.last_checked_at).total_seconds())
        color = '#3fb950' if age <= 60 else '#f85149'
        return format_html('<span style="color:{}">{}s ago</span>', color, age)
    agent_heartbeat_age.short_description = 'Agent heartbeat'

    def active_user_count(self, obj):
        return len(obj.cached_active_sessions or [])
    active_user_count.short_description = 'Active (cached)'

    @admin.action(description='Test connection')
    def test_connection(self, request, queryset):
        for router in queryset:
            status = get_mikrotik_service(router).test_connection()
            router.last_connection_status = 'CONNECTED' if status.connected else 'DISCONNECTED'
            router.last_checked_at = timezone.now()
            router.save(update_fields=['last_connection_status', 'last_checked_at'])
        self.message_user(request, 'Connection test complete — see status column.')


@admin.register(MikroTikProfile)
class MikroTikProfileAdmin(admin.ModelAdmin):
    list_display = ('profile_name', 'router', 'session_timeout', 'mapped_packages')
    list_filter = ('router',)

    def mapped_packages(self, obj):
        names = list(obj.packages.values_list('name', flat=True))
        return ', '.join(names) if names else format_html('<span style="color:#f85149">none — unused</span>')
    mapped_packages.short_description = 'Used by packages'


@admin.register(MikroTikJob)
class MikroTikJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'job_type', 'router', 'status_badge', 'payload_summary', 'created_at', 'completed_at')
    list_filter = ('status', 'job_type', 'router')
    search_fields = ('payload', 'result_detail')
    readonly_fields = ('router', 'job_type', 'payload', 'created_at', 'completed_at')
    actions = ['retry_failed_jobs']

    def status_badge(self, obj):
        colors = {'PENDING': '#d29922', 'DONE': '#3fb950', 'FAILED': '#f85149'}
        return format_html('<span style="color:{}">{}</span>', colors.get(obj.status, '#8b949e'), obj.status)
    status_badge.short_description = 'Status'

    def payload_summary(self, obj):
        username = (obj.payload or {}).get('username', '')
        mac = (obj.payload or {}).get('mac_address', '')
        parts = [p for p in (username, mac) if p]
        return ' / '.join(parts) or '—'
    payload_summary.short_description = 'Payload'

    @admin.action(description='Retry selected failed jobs (re-queue for the agent)')
    def retry_failed_jobs(self, request, queryset):
        updated = queryset.filter(status=MikroTikJob.Status.FAILED).update(
            status=MikroTikJob.Status.PENDING, result_detail='', completed_at=None,
        )
        self.message_user(request, f'{updated} job(s) re-queued.')


@admin.register(InternetSession)
class InternetSessionAdmin(admin.ModelAdmin):
    list_display = (
        'customer', 'payment', 'subscription', 'router', 'status', 'mikrotik_username',
        'ip_address', 'mac_address', 'login_time', 'logout_time',
    )
    list_filter = ('status', 'router')
    search_fields = ('customer__full_name', 'customer__phone_number', 'mikrotik_username', 'mac_address')