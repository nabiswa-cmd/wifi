from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('loyalty', '0002_loyaltyconfig_include_past_purchases'),
    ]

    operations = [
        migrations.AddField(
            model_name='loyaltyconfig',
            name='backdate_past_purchases',
            field=models.BooleanField(default=False, help_text='Only matters with "Include past purchases". OFF (recommended when launching): old payments are credited as of today, so every customer can redeem them and the "points lapse" clock starts now. ON: old payments keep their real payment date, so ones older than the expiry period lapse at once.'),
        ),
    ]
