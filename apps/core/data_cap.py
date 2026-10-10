"""
Total data used by every customer on the hotspot, measured against the
Safaricom plan's monthly cap (e.g. 2 TB for KES 5,000 at 100 Mbps).

Numbers come from the per-device counters the on-site agent already reports
(InternetSession.bytes_downloaded / bytes_uploaded), so they cover customer
devices only. Safaricom's own meter is the final word: it also counts the
router, office devices and anything else on that line.

Set these in the server's .env (restart the web service afterwards):
    ISP_CAP_GB=2048          # plan cap in GB (2 TB = 2048)
    ISP_CYCLE_START_DAY=1    # day of the month your Safaricom plan renews
"""
import calendar
import datetime as dt

from decouple import config
from django.db.models import Sum
from django.utils import timezone

CAP_GB = config('ISP_CAP_GB', default=2048, cast=float)
CYCLE_START_DAY = config('ISP_CYCLE_START_DAY', default=1, cast=int)
GB = 1024 ** 3


def human(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            if unit == 'B':
                return f'{n:.0f} B'
            return f'{n:.2f} {unit}' if unit == 'TB' else f'{n:.1f} {unit}'
        n /= 1024


def _cycle_start_for(year, month, day):
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def cycle_bounds(now=None, start_day=None):
    """(start, end) datetimes of the plan cycle that contains `now` (local midnight to local midnight)."""
    now = timezone.localtime(now or timezone.now())
    day = max(1, min(31, start_day or CYCLE_START_DAY))
    this_month = _cycle_start_for(now.year, now.month, day)
    if now.date() >= this_month:
        start_date = this_month
        ny, nm = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        end_date = _cycle_start_for(ny, nm, day)
    else:
        py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        start_date = _cycle_start_for(py, pm, day)
        end_date = this_month
    make = lambda d: timezone.make_aware(dt.datetime.combine(d, dt.time.min))
    return make(start_date), make(end_date)


def _totals(qs):
    agg = qs.aggregate(down=Sum('bytes_downloaded'), up=Sum('bytes_uploaded'))
    return int(agg['down'] or 0), int(agg['up'] or 0)


def summary(now=None):
    from apps.mikrotik.models import InternetSession

    now = now or timezone.now()
    start, end = cycle_bounds(now)
    counted = InternetSession.objects.filter(last_usage_at__isnull=False)

    down, up = _totals(counted.filter(last_usage_at__gte=start))
    used = down + up
    cap_bytes = int(CAP_GB * GB)
    pct = (used / cap_bytes * 100) if cap_bytes else 0
    all_down, all_up = _totals(counted)

    seconds_left = max(0, (end - now).total_seconds())
    days_left = seconds_left / 86400
    remaining = max(0, cap_bytes - used)

    return {
        'cap_h': human(cap_bytes),
        'used_h': human(used), 'down_h': human(down), 'up_h': human(up),
        'remaining_h': human(remaining),
        'percent': round(pct, 1),
        'bar_percent': min(100, round(pct, 1)),
        'over': used > cap_bytes,
        'start': start, 'end': end,
        'days_left': int(days_left) if days_left >= 1 else round(days_left, 1),
        'per_day_h': human(remaining / days_left) if days_left > 0 else '',
        'all_time_h': human(all_down + all_up),
        'cycle_day': CYCLE_START_DAY,
    }
