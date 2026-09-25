import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vouchers', '0002_voucher_mac_address'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='voucherbatch',
            name='approval_status',
            field=models.CharField(
                choices=[('PENDING', 'Pending approval'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
                default='PENDING',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='voucherbatch',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_voucher_batches', to='accounts.user',
            ),
        ),
        migrations.AddField(
            model_name='voucherbatch',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='voucherbatch',
            name='rejection_reason',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
