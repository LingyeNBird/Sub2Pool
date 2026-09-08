from django.db import migrations, models


def enable_auto_apply(apps, schema_editor):
    apps.get_model("monitor", "AppSettings").objects.using(
        schema_editor.connection.alias
    ).update(auto_apply_recommendations=True)


class Migration(migrations.Migration):
    dependencies = [("monitor", "0052_auto_apply_recommendations")]

    operations = [
        migrations.AlterField(
            model_name="appsettings",
            name="auto_apply_recommendations",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_auto_apply, migrations.RunPython.noop),
    ]
