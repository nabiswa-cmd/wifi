from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='mac_address',
            field=models.CharField(max_length=17, blank=True, default=''),
            preserve_default=False,
        ),
    ]
