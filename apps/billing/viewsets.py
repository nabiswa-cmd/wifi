from rest_framework import viewsets
from apps.core.permissions import HasRolePermission
from .models import Payment, Subscription
from .serializers import PaymentSerializer, SubscriptionSerializer


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """Section 21: staff-only, read-only — payments are only ever mutated by
    the (Phase 3) Daraja callback handler, never through this API."""
    serializer_class = PaymentSerializer
    permission_classes = [HasRolePermission]
    required_permission = 'view_payments'

    def get_queryset(self):
        qs = Payment.objects.select_related('customer', 'package').order_by('-created_at')
        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('customer'):
            qs = qs.filter(customer_id=params['customer'])
        if params.get('phone'):
            qs = qs.filter(phone_number__icontains=params['phone'])
        if params.get('package'):
            qs = qs.filter(package_id=params['package'])
        if params.get('date_from'):
            qs = qs.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(created_at__date__lte=params['date_to'])
        return qs


class SubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    """Section 22: history is never overwritten — renew/extend/suspend all
    create or update rows through Subscription.activate_from_payment() or
    the (future) staff manual-action endpoints, not raw PATCH here."""
    serializer_class = SubscriptionSerializer
    permission_classes = [HasRolePermission]
    required_permission = 'view_reports'

    def get_queryset(self):
        qs = Subscription.objects.select_related('customer', 'package').order_by('-created_at')
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs
