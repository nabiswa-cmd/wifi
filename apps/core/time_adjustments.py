"""
Limits on how much time non-Main-Admin staff (shareholders) may add to a
customer's subscription.

* One adjustment adds at most MAX_MINUTES_PER_ADJUSTMENT minutes (1 hour).
* A staff member gets DAILY_LIMIT adjustments per local day.
* Past that they can ask the Main Admin for more (TimeAdjustmentRequest);
  the Main Admin chooses how many extra adjustments to grant, valid for the
  day the request is approved.

The Main Admin (Role.SUPER_ADMIN) is never limited.

Usage is counted from AuditLog rows with action ADMIN_TIME_ADJUSTMENT, so
the log page and the quota can never disagree.
"""
import datetime as dt

from django.db.models import Sum
from django.utils import timezone

DAILY_LIMIT = 6
MAX_MINUTES_PER_ADJUSTMENT = 60
MAX_GRANT_PER_REQUEST = 50
AUDIT_ACTION = 'ADMIN_TIME_ADJUSTMENT'


def _start_of_today():
    today = timezone.localdate()
    return timezone.make_aware(dt.datetime.combine(today, dt.time.min))


def used_today(user):
    from apps.core.models import AuditLog
    return AuditLog.objects.filter(
        actor=user, action=AUDIT_ACTION, created_at__gte=_start_of_today(),
    ).count()


def extra_granted_today(user):
    from apps.core.models import TimeAdjustmentRequest
    total = TimeAdjustmentRequest.objects.filter(
        requester=user, status=TimeAdjustmentRequest.Status.APPROVED, valid_on=timezone.localdate(),
    ).aggregate(t=Sum('granted_count'))['t']
    return total or 0


def pending_request(user):
    from apps.core.models import TimeAdjustmentRequest
    return TimeAdjustmentRequest.objects.filter(
        requester=user, status=TimeAdjustmentRequest.Status.PENDING,
    ).first()


def allowance(user):
    """Snapshot of today's quota for one staff member."""
    used = used_today(user)
    limit = DAILY_LIMIT + extra_granted_today(user)
    return {
        'used': used,
        'limit': limit,
        'remaining': max(limit - used, 0),
        'base_limit': DAILY_LIMIT,
        'max_minutes': MAX_MINUTES_PER_ADJUSTMENT,
        'pending_request': pending_request(user),
    }
