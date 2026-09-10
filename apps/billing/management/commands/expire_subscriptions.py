"""
Flips ACTIVE subscriptions to EXPIRED the moment they lapse, and enqueues
a DISABLE_USER MikroTik job for each one so the customer actually loses
access — not just a status flag Django-side (Section 10/32).

Meant to run every 1-5 minutes from a persistent worker (see the
"Deploying" section of the README) — this can never run as a Railway/
Vercel serverless request handler, only from a long-lived process, since
nothing else pings it on a schedule.

Usage:
    python manage.py expire_subscriptions
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import Subscription
from apps.mikrotik.models import InternetSession
from apps.mikrotik.services import MikroTikConnectionError, get_mikrotik_service

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Expire subscriptions past their expiry_time and disable them on MikroTik.'

    def handle(self, *args, **options):
        now = timezone.now()
        lapsed = Subscription.objects.filter(
            status=Subscription.Status.ACTIVE,
            expiry_time__isnull=False,
            expiry_time__lte=now,
        )

        count = 0
        for sub in lapsed:
            sub.status = Subscription.Status.EXPIRED
            sub.save(update_fields=['status', 'updated_at'])
            count += 1

            if sub.mikrotik_username:
                session = sub.sessions.filter(status='ACTIVE').select_related('router').first()
                router = session.router if session else None
                if router:
                    try:
                        get_mikrotik_service(router).disable_user(sub.mikrotik_username)
                        if session and session.mac_address:
                            get_mikrotik_service(router).unbypass_mac(session.mac_address)
v
                    logger.warning(
                        'Subscription %s expired but has no active session/router to '
                        'target for disable_user/unbypass_mac — customer may stay '
                        'connected until manually cut off.',
                        sub.id,
                    )

        self.stdout.write(self.style.SUCCESS(f'Expired {count} subscription(s).'))
