"""
Endpoints the on-site agent (agent/agent.py) calls   never the browser,
never the customer-facing app. Authenticated with a single shared secret
(MIKROTIK_AGENT_API_KEY), not staff login, since this is machine-to-
machine and the agent has no user session.

Only the agent may ever mark a MikroTikJob DONE/FAILED   Django itself
never assumes a job succeeded just because it was queued (Section 36).
"""
import json

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import MikroTikJob, MikroTikRouter


def _authenticated(request) -> bool:
    auth = request.headers.get('Authorization', '')
    return auth == f'Bearer {settings.MIKROTIK_AGENT_API_KEY}' and bool(settings.MIKROTIK_AGENT_API_KEY)


@require_GET
def pending_jobs(request):
    if not _authenticated(request):
        return JsonResponse({'detail': 'Unauthorized'}, status=401)

    router = MikroTikRouter.objects.filter(is_active=True).first()
    if not router:
        return JsonResponse({'jobs': []})

    jobs = MikroTikJob.objects.filter(router=router, status=MikroTikJob.Status.PENDING).order_by('created_at')
    return JsonResponse({
        'jobs': [
            {'id': j.id, 'job_type': j.job_type, 'payload': j.payload}
            for j in jobs
        ]
    })


@csrf_exempt
@require_POST
def complete_job(request, job_id):
    if not _authenticated(request):
        return JsonResponse({'detail': 'Unauthorized'}, status=401)

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON'}, status=400)

    status = body.get('status')
    if status not in (MikroTikJob.Status.DONE, MikroTikJob.Status.FAILED):
        return JsonResponse({'detail': 'status must be DONE or FAILED'}, status=400)

    try:
        job = MikroTikJob.objects.get(id=job_id)
    except MikroTikJob.DoesNotExist:
        return JsonResponse({'detail': 'Job not found'}, status=404)

    job.status = status
    job.result_detail = body.get('detail', '')
    job.completed_at = timezone.now()
    job.save(update_fields=['status', 'result_detail', 'completed_at'])
    return JsonResponse({'ok': True})


@csrf_exempt
@require_POST
def heartbeat(request):
    """
    Called by the on-site agent every poll cycle. Besides proving the
    agent is alive (used by test_connection()'s 60s staleness check),
    the agent may optionally attach its latest read of the router's
    hotspot active-users/active-sessions tables   this is the only
    place that live data enters Django, and it's always a snapshot,
    never treated as more current than `last_checked_at` implies.
    """
    if not _authenticated(request):
        return JsonResponse({'detail': 'Unauthorized'}, status=401)

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}

    router = MikroTikRouter.objects.filter(is_active=True).first()
    if router:
        router.last_checked_at = timezone.now()
        router.last_connection_status = MikroTikRouter.ConnectionStatus.CONNECTED
        update_fields = ['last_checked_at', 'last_connection_status']
        if 'active_users' in body:
            router.cached_active_users = body['active_users']
            update_fields.append('cached_active_users')
        if 'active_sessions' in body:
            router.cached_active_sessions = body['active_sessions']
            update_fields.append('cached_active_sessions')
        router.save(update_fields=update_fields)
    return JsonResponse({'ok': True})