from rest_framework import viewsets, permissions
from .models import InternetPackage
from .serializers import InternetPackageSerializer
from apps.core.permissions import HasRolePermission


class InternetPackageViewSet(viewsets.ModelViewSet):
    """
    GET is open (captive portal needs it pre-login). Write access requires
    staff with the manage_packages permission (Section 8/24).
    """
    serializer_class = InternetPackageSerializer
    required_permission = 'manage_packages'

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [HasRolePermission()]

    def get_queryset(self):
        qs = InternetPackage.objects.all().order_by('display_order', 'price')
        if self.request.method in permissions.SAFE_METHODS and not (
            self.request.user.is_authenticated and getattr(self.request.user, 'is_staff_account', False)
        ):
            qs = qs.filter(is_active=True)
        return qs
