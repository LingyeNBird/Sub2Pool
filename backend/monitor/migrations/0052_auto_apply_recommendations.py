from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0051_pooled_research_raw_only")]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="auto_apply_recommendations",
            field=models.BooleanField(default=False),
        ),
    ]
