
"""
Central API router (Section 30). Implemented namespaces use real DRF
viewsets; everything still pending a later phase returns 501 so the URL
contract is fixed now and doesn't shift under the frontend later.
"""
from django.http import JsonResponse
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.packages.viewsets import InternetPackageViewSet
from apps.customers.viewsets import CustomerViewSet
from apps.billing.viewsets import PaymentViewSet, SubscriptionViewSet
from apps.billing.views import mpesa_callback
from apps.mikrotik.views import pending_jobs, complete_job, heartbeat
from apps.core.views import run_scheduled_tasks

router = DefaultRouter()
router.register('packages', InternetPackageViewSet, basename='package')
router.register('customers', CustomerViewSet, basename='customer')
router.register('payments', PaymentViewSet, basename='payment')
router.register('subscriptions', SubscriptionViewSet, basename='subscription')


def not_yet_implemented(request, *args, **kwargs):
    return JsonResponse({'detail': 'Not implemented until a later phase.'}, status=501)


urlpatterns = [
    path('', include(router.urls)),

    path('auth/status/', not_yet_implemented),
    path('mpesa/stkpush/', not_yet_implemented),      # STK push is triggered from /billing/buy/<id>/ instead
    path('mpesa/callback/', mpesa_callback),          # Daraja posts here  see apps.billing.views.mpesa_callback
    path('sessions/', not_yet_implemented),
    path('devices/', not_yet_implemented),
    path('vouchers/', not_yet_implemented),
    path('mikrotik/routers/', not_yet_implemented),   # Phase 4
    path('mikrotik/jobs/pending/', pending_jobs),          # on-site agent polls this
    path('mikrotik/jobs/<int:job_id>/complete/', complete_job),
    path('mikrotik/heartbeat/', heartbeat),
    path('internal/run-scheduled-tasks/', run_scheduled_tasks),  # cron-job.org / any external scheduler hits this
    path('reports/', not_yet_implemented),
    path('notifications/', not_yet_implemented),
    path('settings/', not_yet_implemented),
]
