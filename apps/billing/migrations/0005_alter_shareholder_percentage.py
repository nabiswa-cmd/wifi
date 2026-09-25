from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_shareholder'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shareholder',
            name='percentage',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), editable=False, max_digits=5),
        ),
    ]
