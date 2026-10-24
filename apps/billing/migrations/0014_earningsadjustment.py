import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0013_fup_monitoring'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EarningsAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, help_text="Positive to increase this cycle's earnings, negative to reduce them (e.g. -500).", max_digits=14)),
                ('note', models.CharField(help_text='Why this change is being made.', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('period', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='adjustments', to='billing.earningsperiod')),
            ],
            options={
                'db_table': 'billing_earningsadjustment',
                'ordering': ['-created_at'],
            },
        ),
    ]
