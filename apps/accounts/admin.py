from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User, Role, Permission, StaffProfile


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('username', 'phone_number', 'email', 'is_staff_account', 'is_active', 'date_joined')
    list_filter = ('is_staff_account', 'is_active')
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Platform', {'fields': ('phone_number', 'is_staff_account')}),
    )


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ('codename', 'description')
    search_fields = ('codename',)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    filter_horizontal = ('permissions',)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'employee_id', 'is_active_staff')
    list_filter = ('role', 'is_active_staff')
