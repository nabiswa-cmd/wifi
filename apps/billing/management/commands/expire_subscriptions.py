"""
Flips ACTIVE subscriptions to EXPIRED the moment they lapse, cuts them off
on the router (unbypass_mac + disable_user), and closes their
InternetSession record with a logout_time  so there's a full connect/
disconnect audit trail in the database, not just on the router.

Meant to run every 1-5 minutes from an external scheduler (cron-job.org)
hitting /api/internal/run-scheduled-tasks/.

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
    help = 'Expire subscriptions past their expiry_time, disconnect them, and close their session record.'

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

            # A subscription's pooled time can be shared by SEVERAL
            # different payments (EXTEND renewal)  connect_payment_device
            # now lets each one run its own simultaneous device. When the
            # shared pool runs out, ALL of them lose access together, not
            # just whichever one this used to grab with .first().
            sessions = list(
                sub.sessions.filter(status=InternetSession.Status.ACTIVE).select_related('router')
            )
            if not sessions:
                logger.warning(
                    'Subscription %s expired but has no active session to cut off  '
                    'customer(s) may stay connected until manually disconnected.',
                    sub.id,
                )

            for session in sessions:
                router = session.router
                if router and session.mac_address:
                    try:
                        get_mikrotik_service(router).disable_user(session.mac_address)
                        get_mikrotik_service(router).unbypass_mac(session.mac_address)
                    except MikroTikConnectionError as exc:
                        logger.warning(
                            'Could not queue disable/unbypass for subscription %s, '
                            'device %s: %s', sub.id, session.mac_address, exc,
                        )
                # Close the DB record regardless of router-call success —
                # this is the audit trail, independent of live router state.
                session.status = InternetSession.Status.CLOSED
                session.logout_time = now
                session.save(update_fields=['status', 'logout_time'])

        self.stdout.write(self.style.SUCCESS(f'Expired {count} subscription(s).'))