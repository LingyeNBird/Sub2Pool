from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0018_fast_correction"),
    ]

    operations = [
        migrations.AddField(
            model_name="observation",
            name="fast_correction_request_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="observationfastcorrection",
            name="request_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
