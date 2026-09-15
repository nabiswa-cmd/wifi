from django.contrib import admin
from django.utils.html import format_html
from .models import InternetPackage, PackageProfile


@admin.register(PackageProfile)
class PackageProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'download_speed_kbps', 'upload_speed_kbps', 'session_timeout_seconds')


@admin.register(InternetPackage)
class InternetPackageAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 'duration', 'duration_unit', 'device_limit',
        'is_active', 'is_featured', 'display_order', 'router_profile_status',
    )
    list_filter = ('is_active', 'is_featured', 'duration_unit')
    list_editable = ('display_order', 'is_active', 'is_featured')
    search_fields = ('name',)

    def router_profile_status(self, obj):
        profile = obj.mikrotik_profile
        if not profile:
            return format_html('<span style="color:#f85149">❌ no profile set</span>')
        if not profile.router.is_active:
            return format_html('<span style="color:#d29922">⚠ profile on inactive router ({})</span>', profile.router.name)
        return format_html('<span style="color:#3fb950">✓ {}</span>', profile.profile_name)
    router_profile_status.short_description = 'MikroTik profile'