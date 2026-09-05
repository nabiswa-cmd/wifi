from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('django-admin/', admin.site.urls),          # Django's built-in admin (superusers, low-level)
    path('admin-portal/', include('apps.core.urls')),  # Branded staff dashboard (Section 20)
    path('api/', include('api.urls')),                # DRF API surface (Section 30)
    path('billing/', include('apps.billing.urls')),    # Purchase flow (Section 9/10/11)
    path('', include('apps.customers.urls')),          # Customer-facing captive portal (Section 9)
]
