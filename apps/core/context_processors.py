from .models import SystemSettings


def branding(request):
    """Injects SystemSettings into every template context so business
    name/logo/etc. are never hard-coded (Section 4)."""
    return {'branding': SystemSettings.load()}


def feedback_badge(request):
    """
    Un-contacted customer-feedback count, shown as a small badge next to
    the "Customer Feedback" sidebar link (admin_base.html) so the Main
    Admin/Shareholders notice new messages without opening the page.
    Cheap count() query, and only ever runs for the staff/shareholder
    accounts that can actually see that link.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    profile = getattr(user, 'staff_profile', None)
    if profile is None or not profile.is_active_staff or profile.role.name not in ('SUPER_ADMIN', 'SHAREHOLDER'):
        return {}
    from .models import CustomerFeedback
    return {'feedback_new_count': CustomerFeedback.objects.filter(is_contacted=False).count()}
