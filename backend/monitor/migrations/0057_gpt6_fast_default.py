"""Add the GPT-6 FAST default without repricing history or writing upstream."""
from django.db import migrations


def add_gpt6_fast_default(apps, schema_editor):
    states = apps.get_model("monitor", "UpstreamPricingState").objects.using(
        schema_editor.connection.alias
    )
    for state in states.all():
        rules = state.policy["fast_rules"]
        # Empty means explicit opt-out; a full list or GPT-6 rule is user-owned.
        if not rules or len(rules) >= 100 or any(
            row["model_pattern"].strip().casefold() == "gpt-6*" for row in rules
        ):
            continue
        position = next((
            index for index, row in enumerate(rules)
            if (pattern := row["model_pattern"].strip().casefold()).endswith("*")
            and "gpt-6".startswith(pattern[:-1])
        ), len(rules))
        rules.insert(position, {"model_pattern": "gpt-6*", "multiplier": "2"})
        fields = ["policy"]
        if state.status == "applied":
            state.status = "pending"
            state.last_error = "升级已补入 GPT-6 FAST 2 倍目标规则，尚未写入 Sub2API，请确认应用。"
            fields.extend(["status", "last_error"])
        state.save(using=schema_editor.connection.alias, update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [("monitor", "0056_pricing_announcement_action")]
    operations = [
        migrations.RunPython(add_gpt6_fast_default, migrations.RunPython.noop),
    ]
