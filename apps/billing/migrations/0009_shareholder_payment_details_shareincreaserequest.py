import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('billing', '0008_merge_20260925_1056'),
    ]

    operations = [
        migrations.AddField(
            model_name='shareholder',
            name='payment_phone',
            field=models.CharField(
                blank=True, max_length=20,
                help_text='Default payout phone number, auto-filled onto every new withdrawal request; editable from the shareholder\'s own account page.',
            ),
        ),
        migrations.AddField(
            model_name='shareholder',
            name='payment_account_name',
            field=models.CharField(
                blank=True, max_length=150,
                help_text='Default payout account name, auto-filled onto every new withdrawal request; editable from the shareholder\'s own account page.',
            ),
        ),
        migrations.CreateModel(
            name='ShareIncreaseRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('share_quantity', models.PositiveIntegerField(help_text='Additional shares being requested.')),
                ('contribution_amount', models.DecimalField(
                    decimal_places=2, max_digits=12,
                    help_text="Amount being contributed to fund this increase; added to both the shareholder's own contribution and the company's total capital once approved.",
                )),
                ('note', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(
                    choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
                    db_index=True, default='PENDING', max_length=10,
                )),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('rejection_reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('shareholder', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='share_increase_requests', to='billing.shareholder')),
            ],
            options={
                'db_table': 'billing_shareincreaserequest',
                'ordering': ['-created_at'],
            },
        ),
    ]
