"""
Shared DRF permission classes, implementing the RBAC model from Section 24.
Kept in `core` since every app's viewsets need them.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsStaffAccount(BasePermission):
    """Any authenticated staff user (has a StaffProfile), regardless of role."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'is_staff_account', False)
        )


class HasRolePermission(BasePermission):
    """
    Checks the acting staff user's Role against a permission codename the
    view declares as `required_permission`. SUPER_ADMIN always passes
    (see Role.has_permission). Falls back to read-only for any staff
    account if the view doesn't declare a required_permission.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_staff_account):
            return False
        required = getattr(view, 'required_permission', None)
        if required is None:
            return True
        profile = getattr(request.user, 'staff_profile', None)
        if profile is None or not profile.is_active_staff:
            return False
        if request.method in SAFE_METHODS and getattr(view, 'read_permission_optional', True):
            return True
        return profile.role.has_permission(required)


class IsOwnerCustomer(BasePermission):
    """A customer may only read/write their own Customer-linked records."""

    def has_object_permission(self, request, view, obj):
        customer = getattr(request.user, 'customer_profile', None)
        return customer is not None and getattr(obj, 'customer_id', None) == customer.id
