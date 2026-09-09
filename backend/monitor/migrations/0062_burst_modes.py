from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0061_burst_carry_edits")]

    operations = [
        migrations.AddField(
            model_name="temporaryburstsession",
            name="carryover_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="temporaryburstsession",
            name="terminated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
