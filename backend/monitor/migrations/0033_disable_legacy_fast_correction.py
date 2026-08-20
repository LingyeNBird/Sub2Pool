from django.db import migrations, models


def disable_legacy_fast_correction(apps, _schema_editor):
    AppSettings = apps.get_model("monitor", "AppSettings")
    AppSettings.objects.all().update(
        fast_correction_enabled=False,
        fast_pricing_upgrade_notice_pending=True,
    )


class Migration(migrations.Migration):
    dependencies = [("monitor", "0032_system_user_page_access")]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="fast_pricing_upgrade_notice_pending",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="fast_correction_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            disable_legacy_fast_correction,
            migrations.RunPython.noop,
        ),
    ]
