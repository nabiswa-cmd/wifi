from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mikrotik', '0003_router_cache_and_job_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mikrotikjob',
            name='job_type',
            field=models.CharField(
                choices=[
                    ('CREATE_USER', 'Create hotspot user'),
                    ('DISCONNECT_USER', 'Disconnect user'),
                    ('DISABLE_USER', 'Disable hotspot user'),
                    ('DELETE_USER', 'Delete hotspot user'),
                    ('ACTIVATE_USER', 'Re-enable hotspot user'),
                    ('UPDATE_USER', 'Update hotspot user fields'),
                    ('SET_BANDWIDTH', 'Set bandwidth rate limit'),
                    ('SET_SESSION_TIMEOUT', 'Set session timeout'),
                    ('BYPASS_MAC', 'Grant a device internet without hotspot login'),
                    ('UNBYPASS_MAC', 'Revoke a bypassed device'),
                ],
                max_length=20,
            ),
        ),
    ]
