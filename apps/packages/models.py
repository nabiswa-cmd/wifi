"""
Packages app: database-driven Internet packages and their MikroTik
profile mapping (Sections 8, 15). Nothing here is hard-coded into the
frontend — the eight starter packages in Section 8 are only a fixtures
seed, not code.
"""
from django.db import models


class PackageProfile(models.Model):
    """
    A reusable bandwidth/session template a package can point at, kept
    separate from InternetPackage so the same shaping rule (e.g. '5 Mbps,
    2 devices') can back several priced packages without duplication.
    """
    name = models.CharField(max_length=100, unique=True)
    download_speed_kbps = models.PositiveIntegerField(help_text='Download speed in kbps')
    upload_speed_kbps = models.PositiveIntegerField(help_text='Upload speed in kbps')
    session_timeout_seconds = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = 'packages_packageprofile'

    def __str__(self):
        return self.name


class InternetPackage(models.Model):
    class DurationUnit(models.TextChoices):
        MINUTES = 'MINUTES', 'Minutes'
        HOURS = 'HOURS', 'Hours'
        DAYS = 'DAYS', 'Days'

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration = models.PositiveIntegerField(help_text='Numeric duration, combined with duration_unit')
    duration_unit = models.CharField(max_length=10, choices=DurationUnit.choices, default=DurationUnit.HOURS)

    download_speed_kbps = models.PositiveIntegerField(blank=True, null=True)
    upload_speed_kbps = models.PositiveIntegerField(blank=True, null=True)
    data_allowance_mb = models.PositiveIntegerField(blank=True, null=True)
    unlimited_data = models.BooleanField(default=True)
    device_limit = models.PositiveSmallIntegerField(default=1)

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    # Section 15: the crucial decoupling — Django package name/id never has
    # to match the MikroTik profile name.
    package_profile = models.ForeignKey(
        PackageProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='packages'
    )
    mikrotik_profile = models.ForeignKey(
        'mikrotik.MikroTikProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='packages'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'packages_internetpackage'
        ordering = ['display_order', 'price']

    def __str__(self):
        return f'{self.name} - KSh {self.price}'

    def duration_as_timedelta(self):
        from datetime import timedelta
        if self.duration_unit == self.DurationUnit.MINUTES:
            return timedelta(minutes=self.duration)
        if self.duration_unit == self.DurationUnit.DAYS:
            return timedelta(days=self.duration)
        return timedelta(hours=self.duration)
