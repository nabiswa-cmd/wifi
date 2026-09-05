from .models import SystemSettings


def branding(request):
    """Injects SystemSettings into every template context so business
    name/logo/etc. are never hard-coded (Section 4)."""
    return {'branding': SystemSettings.load()}
