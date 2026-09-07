from rest_framework import serializers
from .models import InternetPackage, PackageProfile


class PackageProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackageProfile
        fields = ['id', 'name', 'download_speed_kbps', 'upload_speed_kbps', 'session_timeout_seconds']


class InternetPackageSerializer(serializers.ModelSerializer):
    """
    Customer-facing shape. Never includes mikrotik_profile internals —
    that mapping is staff-only (Section 15).
    """
    duration_unit_display = serializers.CharField(source='get_duration_unit_display', read_only=True)

    class Meta:
        model = InternetPackage
        fields = [
            'id', 'name', 'description', 'price', 'duration', 'duration_unit',
            'duration_unit_display', 'download_speed_kbps', 'upload_speed_kbps',
            'data_allowance_mb', 'unlimited_data', 'device_limit',
            'is_active', 'is_featured', 'display_order',
        ]
        read_only_fields = fields  # customers never write packages via the API
