from rest_framework import serializers
from .models import Payment, Subscription


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)
    package_name = serializers.CharField(source='package.name', read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'customer', 'customer_name', 'package', 'package_name',
            'phone_number', 'amount', 'checkout_request_id', 'merchant_request_id',
            'mpesa_receipt_number', 'transaction_timestamp', 'status', 'created_at',
        ]
        read_only_fields = fields  # payments are never edited through the API — only via the callback flow


class SubscriptionSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.full_name', read_only=True)
    package_name = serializers.CharField(source='package.name', read_only=True)

    class Meta:
        model = Subscription
        fields = [
            'id', 'customer', 'customer_name', 'package', 'package_name',
            'activation_time', 'expiry_time', 'status', 'activation_source', 'created_at',
        ]
        read_only_fields = fields
