from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='total_capital',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
