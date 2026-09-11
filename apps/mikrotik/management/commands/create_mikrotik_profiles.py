"""
One-shot loader for the router-side hotspot user profiles you've already
created with /ip hotspot user profile add. Creates the matching
MikroTikProfile row for each, and auto-links any InternetPackage whose
duration lines up exactly and unambiguously — anything else is left for
you to assign by hand in admin (Section 15: never guess a mapping).

Usage:
    python manage.py create_mikrotik_profiles
    python manage.py create_mikrotik_profiles --router-id 2   # if you have more than one
"""
from django.core.management.base import BaseCommand, CommandError

from apps.mikrotik.models import MikroTikRouter, MikroTikProfile
from apps.packages.models import InternetPackage

# name, session_timeout (RouterOS format), shared_users, duration in minutes
PROFILES = [
    ('30mins',    '30m', 1, 30),
    ('1 Hour',    '1h',  1, 60),
    ('3 Hours',   '3h',  1, 180),
    ('6 Hours',   '6h',  1, 360),
    ('12 Hours',  '12h', 1, 720),
    ('24 Hours',  '1d',  2, 1440),
    ('2 days',    '2d',  1, 2880),
    ('3 Days',    '3d',  2, 4320),
    ('5days',     '5d',  1, 7200),
    ('7 Days',    '7d',  1, 10080),
    ('15 Days',   '15d', 1, 21600),
    ('30 Days',   '30d', 2, 43200),
]


def package_duration_minutes(pkg):
    unit_minutes = {'MINUTES': 1, 'HOURS': 60, 'DAYS': 1440}
    return pkg.duration * unit_minutes[pkg.duration_unit]


class Command(BaseCommand):
    help = 'Create/update MikroTikProfile rows matching your router-side hotspot user profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--router-id', type=int, default=None,
                             help='Router to attach profiles to. Defaults to the active router.')

    def handle(self, *args, **options):
        router = (
            MikroTikRouter.objects.filter(id=options['router_id']).first()
            if options['router_id'] else
            MikroTikRouter.objects.filter(is_active=True).first()
        )
        if not router:
            raise CommandError('No router found. Pass --router-id or set one active=True in admin first.')

        self.stdout.write(f'Using router: {router.name} (id={router.id})\n')

        created, updated = 0, 0
        profiles_by_id = {}
        for name, timeout, shared_users, minutes in PROFILES:
            profile, was_created = MikroTikProfile.objects.update_or_create(
                router=router, profile_name=name,
                defaults={'session_timeout': timeout},
            )
            profiles_by_id[minutes] = profile
            created += was_created
            updated += not was_created
            self.stdout.write(f'  {"created" if was_created else "updated"}: {name} ({timeout})')

        self.stdout.write(self.style.SUCCESS(f'\n{created} created, {updated} updated.\n'))

        # Auto-link packages whose duration matches exactly one profile above.
        self.stdout.write('Matching packages by duration...\n')
        linked, skipped = 0, []
        for pkg in InternetPackage.objects.all():
            minutes = package_duration_minutes(pkg)
            profile = profiles_by_id.get(minutes)
            if profile and pkg.mikrotik_profile_id != profile.id:
                pkg.mikrotik_profile = profile
                pkg.save(update_fields=['mikrotik_profile', 'updated_at'])
                self.stdout.write(f'  linked: "{pkg.name}" -> {profile.profile_name}')
                linked += 1
            elif not profile:
                skipped.append(pkg.name)

        self.stdout.write(self.style.SUCCESS(f'\n{linked} package(s) linked automatically.'))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f"Couldn't auto-match (no profile with that exact duration) — "
                f"set these manually in admin: {', '.join(skipped)}"
            ))