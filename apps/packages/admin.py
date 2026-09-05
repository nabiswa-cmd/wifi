from django.contrib import admin
from .models import InternetPackage, PackageProfile


@admin.register(PackageProfile)
class PackageProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'download_speed_kbps', 'upload_speed_kbps', 'session_timeout_seconds')


@admin.register(InternetPackage)
class InternetPackageAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 'duration', 'duration_unit', 'device_limit',
        'is_active', 'is_featured', 'display_order', 'mikrotik_profile',
    )
    list_filter = ('is_active', 'is_featured', 'duration_unit')
    list_editable = ('display_order', 'is_active', 'is_featured')
    search_fields = ('name',)
