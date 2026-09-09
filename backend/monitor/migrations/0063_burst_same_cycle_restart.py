from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0062_burst_modes")]

    operations = [
        migrations.RemoveConstraint(
            model_name="temporaryburstcycle", name="unique_burst_account_cycle",
        ),
        migrations.AddConstraint(
            model_name="temporaryburstcycle",
            constraint=models.UniqueConstraint(
                fields=("session", "account", "resets_at"),
                name="unique_burst_session_account_cycle",
            ),
        ),
    ]
