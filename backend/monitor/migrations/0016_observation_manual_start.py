from django.db import migrations, models
import django.core.validators
from decimal import Decimal


def migrate_manual_starts(apps, _schema_editor):
    Observation = apps.get_model("monitor", "Observation")
    Observation.objects.update(
        interval_used_percent=models.F("upstream_used_percent"),
    )
    for observation in Observation.objects.filter(force_included=True).iterator():
        observation.is_manual_start = True
        observation.manual_start_reason = "由旧版管理员恢复的回退记录迁移"
        observation.manual_start_set_at = observation.created_at
        observation.save(
            update_fields=[
                "is_manual_start",
                "manual_start_reason",
                "manual_start_set_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("monitor", "0015_replay_architecture")]

    operations = [
        migrations.AddField(
            model_name="observation",
            name="interval_used_percent",
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=8,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="observation",
            name="is_manual_start",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="observation",
            name="manual_start_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="observation",
            name="manual_start_set_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            migrate_manual_starts,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="observation",
            name="force_included",
        ),
    ]
