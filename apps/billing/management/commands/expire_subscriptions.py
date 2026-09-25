"""
Flips ACTIVE subscriptions to EXPIRED the moment they lapse and cuts off
every device on them (Subscription.cutoff_all_devices)  the same method
used when a subscription is CANCELLED outright by a renewal, so this
logic can't silently diverge between the two paths again.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import Subscription


class Command(BaseCommand):
    help = 'Expire subscriptions past their expiry_time and cut off every device on them.'

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
            sub.cutoff_all_devices()
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Expired {count} subscription(s).'))