from django.core.management.base import BaseCommand

from apps.mikrotik.models import MikroTikRouter, MikroTikProfile
from apps.packages.models import InternetPackage

NEW_PLAN = [
    (30,    5,   '30 Minutes'),
    (60,    10,  '1 Hour'),
    (180,   15,  '3 Hours'),
    (720,   25,  '12 Hours'),
    (1440,  35,  '24 Hours'),
    (2880,  70,  '2 Days'),
    (4320,  105, '3 Days'),
    (7200,  180, '5 Days'),
    (10080, 200, '7 Days'),
    (21600, 300, '15 Days'),
    (43200, 450, '30 Days'),
]


def to_duration(minutes):
    if minutes % 1440 == 0:
        return minutes // 1440, InternetPackage.DurationUnit.DAYS
    if minutes % 60 == 0:
        return minutes // 60, InternetPackage.DurationUnit.HOURS
    return minutes, InternetPackage.DurationUnit.MINUTES


def package_minutes(pkg):
    return pkg.duration * {'MINUTES': 1, 'HOURS': 60, 'DAYS': 1440}[pkg.duration_unit]


def profile_minutes(session_timeout):
    unit = session_timeout[-1]
    value = int(session_timeout[:-1])
    return value * {'m': 1, 'h': 60, 'd': 1440}[unit]


class Command(BaseCommand):
    help = 'Apply the new package pricing plan.'

    def handle(self, *args, **options):
        router = MikroTikRouter.objects.filter(is_active=True).first()
        keep_minutes = {m for m, _, _ in NEW_PLAN}

        for minutes, price, label in NEW_PLAN:
            duration, unit = to_duration(minutes)
            existing = next((p for p in InternetPackage.objects.all() if package_minutes(p) == minutes), None)
            profile = next(
                (p for p in MikroTikProfile.objects.filter(router=router) if profile_minutes(p.session_timeout) == minutes),
                None,
            ) if router else None

            if existing:
                existing.price = price
                existing.is_active = True
                if profile:
                    existing.mikrotik_profile = profile
                existing.save(update_fields=['price', 'is_active', 'mikrotik_profile', 'updated_at'])
                self.stdout.write(f'  updated: {existing.name} -> KSh {price}')
            else:
                InternetPackage.objects.create(
                    name=label, price=price, duration=duration, duration_unit=unit, mikrotik_profile=profile,
                )
                self.stdout.write(f'  created: {label} -> KSh {price}')

        deactivated = []
        for pkg in InternetPackage.objects.filter(is_active=True):
            if package_minutes(pkg) not in keep_minutes:
                pkg.is_active = False
                pkg.save(update_fields=['is_active', 'updated_at'])
                deactivated.append(pkg.name)
        if deactivated:
            self.stdout.write(self.style.WARNING(f'Deactivated (not in new plan): {", ".join(deactivated)}'))
        self.stdout.write(self.style.SUCCESS('Done.'))