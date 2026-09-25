from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import HasRolePermission
from apps.core.audit import log_action
from .models import Customer
from .serializers import CustomerAdminSerializer


class CustomerViewSet(viewsets.ModelViewSet):
    """Staff-only customer management (Section 7). Customer self-service
    goes through apps.customers.views.customer_dashboard / a future
    /api/customers/me/ endpoint, not this viewset."""
    queryset = Customer.objects.all().order_by('-registration_date')
    serializer_class = CustomerAdminSerializer
    permission_classes = [HasRolePermission]
    required_permission = 'manage_customers'
    read_permission_optional = True  # any staff can view; only manage_customers can write

    def perform_update(self, serializer):
        before = CustomerAdminSerializer(serializer.instance).data
        instance = serializer.save()
        log_action(
            actor=self.request.user, action='customer_edited', obj=instance,
            previous_value=before, new_value=CustomerAdminSerializer(instance).data,
        )

    @action(detail=True, methods=['post'])
    def suspend(self, request, pk=None):
        customer = self.get_object()
        customer.status = Customer.Status.SUSPENDED
        customer.save(update_fields=['status', 'updated_at'])
        log_action(actor=request.user, action='customer_suspended', obj=customer)
        return Response(CustomerAdminSerializer(customer).data)

    @action(detail=True, methods=['post'])
    def reactivate(self, request, pk=None):
        customer = self.get_object()
        customer.status = Customer.Status.ACTIVE
        customer.save(update_fields=['status', 'updated_at'])
        log_action(actor=request.user, action='customer_reactivated', obj=customer)
        return Response(CustomerAdminSerializer(customer).data)

    @action(detail=True, methods=['post'])
    def disconnect_session(self, request, pk=None):
        """Manually disconnect a customer's active session (Section 7/17).
        Goes through MikroTikService so it's never a direct API call from a view."""
        from apps.mikrotik.services import get_mikrotik_service, MikroTikConnectionError
        customer = self.get_object()
        active_session = customer.sessions.filter(status='ACTIVE').first()
        if not active_session or not active_session.router:
            return Response({'detail': 'No active session with a known router.'}, status=status.HTTP_400_BAD_REQUEST)
        service = get_mikrotik_service(active_session.router)
        try:
            service.disconnect_user(active_session.mikrotik_username)
        except MikroTikConnectionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        active_session.status = 'CLOSED'
        active_session.save(update_fields=['status'])
        log_action(actor=request.user, action='session_disconnected', obj=active_session)
        return Response({'detail': 'Disconnected.'})
