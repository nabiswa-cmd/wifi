"""
Accounts app: the platform's identity + RBAC layer (Sections 6, 24).

User            - single custom auth user for both staff and customer login
Role            - named bundle of permissions (SUPER ADMIN, ADMIN, OPERATOR, SUPPORT, FINANCE)
Permission      - a single grantable capability (manage_customers, manage_packages, ...)
StaffProfile    - staff-specific data hanging off a User with a Role
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user so we control the schema from day one (avoids a painful
    mid-project migration). Customers and staff both authenticate through
    this model; `is_staff_account` distinguishes them at the application level
    (separate from Django's own `is_staff`, which only gates /django-admin/).
    """
    phone_number = models.CharField(max_length=20, blank=True, db_index=True)
    is_staff_account = models.BooleanField(default=False)

    class Meta:
        db_table = 'accounts_user'

    def __str__(self):
        return self.username


class Permission(models.Model):
    """A single named capability. Deliberately app-defined (not Django's
    built-in auth.Permission) so it maps 1:1 onto the capabilities listed in
    Section 24 (manage_customers, manage_packages, view_payments, ...)."""
    codename = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'accounts_permission'
        ordering = ['codename']

    def __str__(self):
        return self.codename


class Role(models.Model):
    class RoleName(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
        ADMIN = 'ADMIN', 'Admin'
        OPERATOR = 'OPERATOR', 'Operator'
        SUPPORT = 'SUPPORT', 'Support'
        FINANCE = 'FINANCE', 'Finance'

    name = models.CharField(max_length=20, choices=RoleName.choices, unique=True)
    permissions = models.ManyToManyField(Permission, related_name='roles', blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'accounts_role'

    def __str__(self):
        return self.get_name_display()

    def has_permission(self, codename: str) -> bool:
        if self.name == self.RoleName.SUPER_ADMIN:
            return True
        return self.permissions.filter(codename=codename).exists()


class StaffProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='staff')
    employee_id = models.CharField(max_length=32, unique=True, blank=True, null=True)
    is_active_staff = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_staffprofile'

    def __str__(self):
        return f'{self.user.username} ({self.role})'
