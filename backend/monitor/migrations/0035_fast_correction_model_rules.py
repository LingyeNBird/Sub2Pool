from django.db import migrations, models

import monitor.fast_correction.rules


def restore_fast_correction(apps, _schema_editor):
    AppSettings = apps.get_model("monitor", "AppSettings")
    AppSettings.objects.all().update(fast_correction_enabled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0034_announcement_reads"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="fast_correction_rules",
            field=models.JSONField(
                default=monitor.fast_correction.rules.default_fast_correction_rules,
                validators=[
                    monitor.fast_correction.rules.validate_fast_correction_rules
                ],
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="fast_correction_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="participantapiusagesnapshot",
            name="fast_correction_rules_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RunPython(
            restore_fast_correction,
            migrations.RunPython.noop,
        ),
    ]
