"""
Watchdog for the agent/router link. Two jobs:

1. Mark routers DISCONNECTED if the on-site agent hasn't heartbeated
   recently (the agent itself only ever reports CONNECTED — someone has
   to notice when it goes quiet).
2. Re-queue MikroTikJob rows that have sat PENDING too long, which
   usually means the agent was down when they were created and needs a
   nudge on reconnect (the agent will just pick these up on its next
   poll regardless, but this surfaces genuinely stuck jobs in the log
   instead of them silently sitting there forever).

Run this alongside expire_subscriptions from the same persistent worker.

Usage:
    python manage.py sync_mikrotik
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mikrotik.models import MikroTikJob, MikroTikRouter

logger = logging.getLogger(__name__)

STALE_HEARTBEAT_SECONDS = 90
STUCK_JOB_MINUTES = 10


class Command(BaseCommand):
    help = 'Mark routers with a stale agent heartbeat as disconnected; flag stuck jobs.'

    def handle(self, *args, **options):
        now = timezone.now()
        stale_count = 0

        for router in MikroTikRouter.objects.filter(is_active=True):
            if not router.last_checked_at:
                continue
            age = (now - router.last_checked_at).total_seconds()
            if age > STALE_HEARTBEAT_SECONDS and router.last_connection_status != MikroTikRouter.ConnectionStatus.DISCONNECTED:
                router.last_connection_status = MikroTikRouter.ConnectionStatus.DISCONNECTED
                router.save(update_fields=['last_connection_status'])
                stale_count += 1
                logger.warning(
                    'Router %s marked DISCONNECTED — no agent heartbeat in %ds.',
                    router.name, int(age),
                )

        stuck_cutoff = now - timezone.timedelta(minutes=STUCK_JOB_MINUTES)
        stuck_jobs = MikroTikJob.objects.filter(
            status=MikroTikJob.Status.PENDING, created_at__lte=stuck_cutoff
        )
        stuck_count = stuck_jobs.count()
        if stuck_count:
            logger.warning(
                '%d MikroTikJob(s) have been PENDING for over %d minutes — '
                'agent is likely offline. They will run automatically once '
                'it reconnects; no action needed unless it stays down.',
                stuck_count, STUCK_JOB_MINUTES,
            )

        self.stdout.write(self.style.SUCCESS(
            f'{stale_count} router(s) marked stale, {stuck_count} job(s) stuck pending.'
        ))
