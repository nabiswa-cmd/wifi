import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('billing', '0003_payment_mac_address'),
    ]

    operations = [
        migrations.CreateModel(
            name='Shareholder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(blank=True, max_length=150)),
                ('share_quantity', models.PositiveIntegerField(default=0)),
                ('contribution', models.DecimalField(decimal_places=2, default='0.00', max_digits=12)),
                ('percentage', models.DecimalField(decimal_places=2, default='0.00', max_digits=5)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='shareholder_profile', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'billing_shareholder',
                'ordering': ['-percentage'],
            },
        ),
    ]
