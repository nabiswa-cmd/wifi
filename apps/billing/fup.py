"""
Fair Usage Policy (FUP) MONITORING. Read-only with respect to the network:
nothing in this module queues a MikroTikJob, disconnects anyone, changes a
speed or edits a profile. It only records what was used and raises warnings.

Where the numbers come from
---------------------------
The on-site agent already talks to the router. Once a minute it adds the
router's own per-device byte counters (`host_usage`) to the heartbeat it
already sends. `record_usage_samples()` turns those running counters into
per-device totals stored on `InternetSession`. No usage figure is ever
estimated from subscription time or package price: a device the router has
not reported has no usage recorded, and the dashboard says so.

Router counters are cumulative and reset when the router forgets a device,
so we store the previous counter and add only the difference. The first time
a session is seen we only set that baseline (we cannot know how much of the
counter belongs to this subscription), so up to one sampling interval of
traffic at the very start is not counted. Totals are therefore a close
lower bound, never an overcount.
"""
import logging
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import FUPAlert, Subscription

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024 * 1024

# A device counts as "online now" if the router's host table listed it within this window.
# The agent samples every 60 s, so 3 minutes tolerates one missed sample.
ONLINE_WINDOW = timedelta(minutes=3)

# Lowest to highest. Status names match the dashboard labels.
NORMAL, APPROACHING, HIGH, REACHED = 'NORMAL', 'APPROACHING', 'HIGH', 'REACHED'
NO_LIMIT = 'NO_LIMIT'
STATUS_LABELS = {
    NORMAL: 'Normal',
    APPROACHING: 'Approaching FUP',
    HIGH: 'High usage',
    REACHED: 'FUP reached',
    NO_LIMIT: 'No FUP limit',
}
# (minimum percent, status, FUPAlert threshold)
_BANDS = (
    (100, REACHED, FUPAlert.Threshold.REACHED),
    (85, HIGH, FUPAlert.Threshold.HIGH),
    (70, APPROACHING, FUPAlert.Threshold.APPROACHING),
)

_MAC_RE = re.compile(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$')


def classify(percent):
    """Status band for a usage percentage: <70 Normal, 70-84 Approaching, 85-99 High, 100+ Reached."""
    for minimum, status, _threshold in _BANDS:
        if percent >= minimum:
            return status
    return NORMAL


def percent_used(used_bytes, limit_mb):
    if not limit_mb:
        return None
    return used_bytes * 100.0 / (limit_mb * BYTES_PER_MB)


def _clean_sample(raw):
    """Validate one agent-reported row; returns (mac, bytes_in, bytes_out) or None to skip it."""
    try:
        mac = str(raw.get('mac_address', '')).upper().replace('-', ':')
        bytes_in = int(raw.get('bytes_in'))
        bytes_out = int(raw.get('bytes_out'))
    except (AttributeError, TypeError, ValueError):
        return None
    if not _MAC_RE.match(mac) or bytes_in < 0 or bytes_out < 0:
        return None
    return mac, bytes_in, bytes_out


def _delta(current, previous):
    """Counter difference. A counter that went DOWN was reset by the router, so everything now showing is new."""
    if previous is None:
        return 0  # first sighting: baseline only
    return current - previous if current >= previous else current


def record_usage_samples(samples, now=None):
    """
    Fold one agent sample into the per-session totals, then check thresholds
    for the subscriptions that were touched. Idempotent: re-sending the same
    counters adds 0. Returns the number of sessions updated.
    """
    from apps.mikrotik.models import InternetSession

    now = now or timezone.now()
    cleaned = {}
    for raw in samples or []:
        row = _clean_sample(raw)
        if row:
            cleaned[row[0]] = row  # one row per MAC; last one wins

    touched_subscriptions = {}
    updated = 0
    for mac, bytes_in, bytes_out in cleaned.values():
        with transaction.atomic():
            # The device's current session is the most recent ACTIVE one on a live subscription.
            session = (
                InternetSession.objects.select_for_update()
                .filter(mac_address=mac, status=InternetSession.Status.ACTIVE, subscription__isnull=False)
                .order_by('-login_time', '-id')
                .first()
            )
            if session is None:
                continue  # not one of our customers' devices (or no live session): nothing to attribute
            # Router "bytes-in" = received FROM the device = the customer's upload.
            session.bytes_uploaded += _delta(bytes_in, session.last_counter_in)
            session.bytes_downloaded += _delta(bytes_out, session.last_counter_out)
            session.last_counter_in = bytes_in
            session.last_counter_out = bytes_out
            session.last_usage_at = now
            session.save(update_fields=[
                'bytes_uploaded', 'bytes_downloaded', 'last_counter_in', 'last_counter_out', 'last_usage_at',
            ])
            touched_subscriptions[session.subscription_id] = True
            updated += 1

    if touched_subscriptions:
        subs = Subscription.objects.filter(pk__in=touched_subscriptions).select_related('package')
        for sub in subs:
            check_thresholds(sub)
    return updated


def subscription_usage(subscription):
    """(uploaded, downloaded) bytes recorded across every session of this subscription."""
    totals = subscription.sessions.aggregate(
        up=Coalesce(Sum('bytes_uploaded'), 0), down=Coalesce(Sum('bytes_downloaded'), 0),
    )
    return totals['up'], totals['down']


def check_thresholds(subscription):
    """
    Create a FUPAlert if usage has crossed a threshold that has not been
    alerted yet for this subscription. Only the HIGHEST crossed threshold is
    recorded (jumping from 60% to 90% yields one 85% alert, not two), and a
    lower threshold is never raised after a higher one. Returns the alert or None.
    """
    limit_mb = subscription.package.fup_limit_mb
    if not limit_mb:
        return None
    up, down = subscription_usage(subscription)
    used = up + down
    percent = percent_used(used, limit_mb)
    for minimum, _status, threshold in _BANDS:
        if percent >= minimum:
            if FUPAlert.objects.filter(subscription=subscription, threshold__gte=threshold).exists():
                return None
            try:
                with transaction.atomic():
                    return FUPAlert.objects.create(
                        subscription=subscription, threshold=threshold, usage_bytes=used, limit_mb=limit_mb,
                    )
            except IntegrityError:
                return None  # a concurrent heartbeat created it first
    return None


def dashboard_rows(now=None):
    """
    One row per subscription that is entitled right now, with recorded usage.
    `online` comes only from the router's own report (last_usage_at), never
    from the subscription being active.
    """
    now = now or timezone.now()
    subs = (
        Subscription.objects.filter(status=Subscription.Status.ACTIVE, expiry_time__gt=now)
        .select_related('customer', 'package')
        .annotate(
            up=Coalesce(Sum('sessions__bytes_uploaded'), 0),
            down=Coalesce(Sum('sessions__bytes_downloaded'), 0),
            last_seen=Max('sessions__last_usage_at'),
        )
    )
    rows = []
    for sub in subs:
        used = sub.up + sub.down
        limit_mb = sub.package.fup_limit_mb
        percent = percent_used(used, limit_mb)
        status = classify(percent) if percent is not None else NO_LIMIT
        rows.append({
            'subscription': sub,
            'customer': sub.customer,
            'package': sub.package,
            'uploaded': sub.up,
            'downloaded': sub.down,
            'used': used,
            'limit_mb': limit_mb,
            'percent': percent,
            'bar_percent': min(percent, 100) if percent is not None else 0,
            'status': status,
            'status_label': STATUS_LABELS[status],
            'last_seen': sub.last_seen,
            'online': bool(sub.last_seen and now - sub.last_seen <= ONLINE_WINDOW),
            'has_data': sub.last_seen is not None,
        })
    rows.sort(key=lambda r: (r['percent'] is None, -(r['percent'] or 0), -r['used']))
    return rows


def human_bytes(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
