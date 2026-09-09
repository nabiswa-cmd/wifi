from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mikrotik', '0002_mikrotikjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='mikrotikrouter',
            name='cached_active_users',
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name='mikrotikrouter',
            name='cached_active_sessions',
            field=models.JSONField(default=list, blank=True),
        ),
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
                ],
                max_length=20,
            ),
        ),
    ]
