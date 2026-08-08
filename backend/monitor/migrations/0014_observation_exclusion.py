from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0013_participantsnapshot_recommendation_applied"),
    ]

    operations = [
        migrations.AddField(
            model_name="observation",
            name="excluded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="observation",
            name="exclusion_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
