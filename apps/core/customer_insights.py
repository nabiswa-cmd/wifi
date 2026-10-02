"""
One place that defines "who counts as what" for customers, so a dashboard
tile and the list it links to can never disagree.

Each filter below is used in two places:
  * the dashboard tile shows ``count(key)``
  * the Customers page shows the rows of ``customers(key)``

Purchase history is worked out from Subscription rows (every package a
customer has ever been given: M-Pesa, voucher or staff-added), so voucher
customers are counted too. Money comes from successful M-Pesa Payments.
"""
from collections import OrderedDict
from decimal import Decimal

from django.db.models import Count, DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

RECENT_DAYS = 7      # bought within this many days  -> "Recent buyer"
LAPSED_DAYS = 30     # nothing bought for this long  -> "Lapsed"


# --------------------------------------------------------------------------
# Purchase history helpers (shared with the Subscriptions page)
# --------------------------------------------------------------------------
def _customer_subs(outer_field='pk'):
    from apps.billing.models import Subscription
    return Subscription.objects.filter(customer=OuterRef(outer_field)).exclude(
        status=Subscription.Status.PENDING
    )


def purchase_annotations(outer_field='pk'):
    """
    Annotations giving a customer's number of purchases and when they last
    bought. ``outer_field`` is 'pk' on Customer querysets and 'customer_id'
    on Subscription querysets.
    """
    subs = _customer_subs(outer_field).order_by()
    return {
        'purchase_count': Coalesce(
            Subquery(subs.values('customer').annotate(c=Count('id')).values('c')), 0
        ),
        'last_purchase': Subquery(
            _customer_subs(outer_field).order_by('-created_at').values('created_at')[:1]
        ),
    }


def ago(when, now=None):
    """'Today', 'Yesterday', '5 days ago', '3 weeks ago' ... or '' if never."""
    if when is None:
        return ''
    now = now or timezone.now()
    days = (timezone.localtime(now).date() - timezone.localtime(when).date()).days
    if days <= 0:
        return 'Today'
    if days == 1:
        return 'Yesterday'
    if days < 14:
        return f'{days} days ago'
    if days < 60:
        return f'{days // 7} weeks ago'
    if days < 730:
        return f'{days // 30} months ago'
    return f'{days // 365} years ago'


def recency(when, now=None):
    """(key, label) describing how recently someone bought."""
    if when is None:
        return 'never', 'Never bought'
    now = now or timezone.now()
    age = (now - when).days
    if age <= RECENT_DAYS:
        return 'recent', 'Recent buyer'
    if age <= LAPSED_DAYS:
        return 'cooling', 'Cooling off'
    return 'lapsed', 'Lapsed'


def recency_q(key, field='last_purchase', now=None):
    """Q object selecting one recency bucket on an annotated queryset."""
    now = now or timezone.now()
    recent_cut = now - timezone.timedelta(days=RECENT_DAYS)
    lapsed_cut = now - timezone.timedelta(days=LAPSED_DAYS)
    if key == 'recent':
        return Q(**{f'{field}__gte': recent_cut})
    if key == 'cooling':
        return Q(**{f'{field}__lt': recent_cut, f'{field}__gte': lapsed_cut})
    if key == 'lapsed':
        return Q(**{f'{field}__lt': lapsed_cut})
    if key == 'never':
        return Q(**{f'{field}__isnull': True})
    return None


# --------------------------------------------------------------------------
# Customer queryset + named filters
# --------------------------------------------------------------------------
def customer_queryset(now=None):
    from apps.billing.models import Payment, Subscription
    from apps.customers.models import Customer
    from apps.mikrotik.models import InternetSession

    now = now or timezone.now()
    today = timezone.localdate()
    money = DecimalField(max_digits=12, decimal_places=2)

    paid = Payment.objects.filter(customer=OuterRef('pk'), status=Payment.Status.SUCCESS).order_by()
    paid_up = Subscription.objects.filter(customer=OuterRef('pk'), status=Subscription.Status.ACTIVE).filter(
        Q(expiry_time__gt=now) | Q(expiry_time__isnull=True)
    )
    return Customer.objects.annotate(
        **purchase_annotations('pk'),
        total_paid=Coalesce(
            Subquery(paid.values('customer').annotate(t=Sum('amount')).values('t'), output_field=money),
            Value(Decimal('0.00')), output_field=money,
        ),
        has_paid_package=Exists(paid_up),
        is_online=Exists(InternetSession.objects.filter(customer=OuterRef('pk'), status=InternetSession.Status.ACTIVE)),
        connected_today=Exists(InternetSession.objects.filter(customer=OuterRef('pk'), login_time__date=today)),
    )


# key -> (label, plain-English meaning, bootstrap icon, filter function)
FILTERS = OrderedDict([
    ('all', (
        'All customers', 'Everyone in the system: anyone who has ever connected or bought a package.',
        'bi-people-fill', lambda qs, now: qs)),
    ('active_account', (
        'Active customers',
        'Accounts in good standing (not suspended, blocked or inactive). This is about the account only; '
        'it does not mean they have internet time left.',
        'bi-person-check-fill', lambda qs, now: qs.filter(status='ACTIVE'))),
    ('subscribed', (
        'Active users', 'Customers with a paid package that has not run out yet, so they can use the internet.',
        'bi-wifi', lambda qs, now: qs.filter(has_paid_package=True))),
    ('online', (
        'Online now', 'Customers with a device connected to the hotspot at this moment.',
        'bi-broadcast', lambda qs, now: qs.filter(is_online=True))),
    ('connected_today', (
        'Connected today', 'Customers who connected to the hotspot at least once today.',
        'bi-box-arrow-in-right', lambda qs, now: qs.filter(connected_today=True))),
    ('expired', (
        'Expired customers', 'Customers whose last package has run out and who have not renewed.',
        'bi-person-x-fill', lambda qs, now: qs.filter(package_expiry__lt=now))),
    ('recent', (
        f'Bought in last {RECENT_DAYS} days', 'Customers who bought or were given a package this week.',
        'bi-bag-check-fill', lambda qs, now: qs.filter(recency_q('recent', now=now)))),
    ('lapsed', (
        f'Lapsed ({LAPSED_DAYS}+ days)', f'Customers who bought before but nothing in the last {LAPSED_DAYS} days. '
        'Good people to reach out to.',
        'bi-clock-history', lambda qs, now: qs.filter(recency_q('lapsed', now=now)))),
    ('repeat', (
        'Repeat customers', 'Customers who have bought two or more times.',
        'bi-arrow-repeat', lambda qs, now: qs.filter(purchase_count__gte=2))),
    ('never', (
        'Never bought', 'Customers who registered or connected but never had a package.',
        'bi-person-dash-fill', lambda qs, now: qs.filter(purchase_count=0))),
])

SORTS = OrderedDict([
    ('recent', ('Bought most recently', [F('last_purchase').desc(nulls_last=True), '-registration_date'])),
    ('lapsed', ('Longest since last purchase', [F('last_purchase').asc(nulls_last=True), '-registration_date'])),
    ('most', ('Most purchases', ['-purchase_count', F('last_purchase').desc(nulls_last=True)])),
    ('spend', ('Highest spenders', ['-total_paid', F('last_purchase').desc(nulls_last=True)])),
    ('newest', ('Newest customers', ['-registration_date'])),
])


def customers(key='all', now=None):
    now = now or timezone.now()
    func = FILTERS.get(key, FILTERS['all'])[3]
    return func(customer_queryset(now), now)


def count(key, now=None):
    return customers(key, now).count()
