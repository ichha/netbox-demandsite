from django.db import migrations, models

class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='DemandSite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'verbose_name': 'Demand Site',
                'verbose_name_plural': 'Demand Sites',
                'managed': False,
                'default_permissions': ('add', 'change', 'delete', 'view'),
                'permissions': (
                    ('sync_demandsite', 'Can sync site data from Demandsite API'),
                ),
            },
        ),
    ]
