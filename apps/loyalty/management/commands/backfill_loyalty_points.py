"""
Give loyalty points for old M-Pesa payments that were made before the programme
(or the "Include past purchases" setting) was switched on.

    python manage.py backfill_loyalty_points --dry-run   # just count
    python manage.py backfill_loyalty_points             # do it

Safe to run repeatedly: a payment can only ever earn points once. Customers are
also credited automatically when they open the "My points" section, so running
this is optional; it just brings everyone up to date in one go.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.loyalty import services


class Command(BaseCommand):
    help = 'Credit loyalty points for successful payments made before the programme was switched on.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only count; change nothing.')

    def handle(self, *args, **opts):
        try:
            result = services.backfill_all(dry_run=opts['dry_run'])
        except services.LoyaltyError as exc:
            raise CommandError(str(exc))
        if opts['dry_run']:
            self.stdout.write(f"Would credit up to {result['points']} points across {result['customers']} customer(s) "
                              f'(before any expiry).')
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Credited {result['points']} points to {result['customers']} customer(s)."))
