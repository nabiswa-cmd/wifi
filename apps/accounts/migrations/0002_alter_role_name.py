from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='name',
            field=models.CharField(
                choices=[
                    ('SUPER_ADMIN', 'Super Admin'),
                    ('ADMIN', 'Admin'),
                    ('OPERATOR', 'Operator'),
                    ('SUPPORT', 'Support'),
                    ('FINANCE', 'Finance'),
                    ('SHAREHOLDER', 'Shareholder'),
                ],
                max_length=20,
                unique=True,
            ),
        ),
    ]
