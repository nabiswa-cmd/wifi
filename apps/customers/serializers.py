from rest_framework import serializers
from .models import Customer, Device


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ['id', 'mac_address', 'ip_address', 'device_type', 'first_seen', 'last_seen', 'is_active']
        read_only_fields = ['first_seen', 'last_seen']


class CustomerSelfSerializer(serializers.ModelSerializer):
    """What a logged-in customer sees about themselves (Section 27) — no
    staff-only fields like `notes`."""
    devices = DeviceSerializer(many=True, read_only=True)
    current_package_name = serializers.CharField(source='current_package.name', read_only=True)

    class Meta:
        model = Customer
        fields = [
            'id', 'full_name', 'phone_number', 'email', 'username', 'status',
            'current_package', 'current_package_name', 'package_expiry',
            'total_spending', 'devices',
        ]
        read_only_fields = fields


class CustomerAdminSerializer(serializers.ModelSerializer):
    """Full shape for staff (Section 7)."""
    class Meta:
        model = Customer
        fields = [
            'id', 'full_name', 'phone_number', 'email', 'username', 'status',
            'registration_date', 'last_login_at', 'current_package', 'package_expiry',
            'total_spending', 'notes', 'mikrotik_username', 'mac_address',
            'ip_address', 'device_info',
        ]
        read_only_fields = ['registration_date', 'last_login_at', 'current_package', 'package_expiry', 'total_spending']
