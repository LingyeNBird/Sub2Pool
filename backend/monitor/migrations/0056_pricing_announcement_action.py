from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0055_upstream_pricing_group_selection")]
    operations = [
        migrations.AddField(
            model_name="upstreampricingstate",
            name="announcement_applied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
