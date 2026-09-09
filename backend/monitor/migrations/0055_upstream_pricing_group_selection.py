from django.db import migrations, models


def preserve_existing_selection(apps, schema_editor):
    State = apps.get_model("monitor", "UpstreamPricingState")
    for state in State.objects.using(schema_editor.connection.alias).all():
        state.selected_group_ids = sorted({
            row["group_id"] for row in state.targets
            if row.get("status") != "reverted" and isinstance(row.get("group_id"), int)
        })
        state.save(update_fields=["selected_group_ids"])


class Migration(migrations.Migration):
    dependencies = [("monitor", "0054_upstream_pricing_cutover")]
    operations = [
        migrations.AddField(
            model_name="upstreampricingstate", name="selected_group_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(preserve_existing_selection, migrations.RunPython.noop),
    ]
