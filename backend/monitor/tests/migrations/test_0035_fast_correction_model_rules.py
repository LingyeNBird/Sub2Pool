import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


FROM = [("monitor", "0034_announcement_reads")]
TO = [("monitor", "0035_fast_correction_model_rules")]
DEFAULT_RULES = [
    {
        "model_pattern": "*",
        "source_multiplier": "2",
        "target_multiplier": "2.5",
    }
]


@pytest.mark.django_db(transaction=True)
def test_upgrade_restores_fast_correction_with_default_model_rule():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    OldSettings = old_apps.get_model("monitor", "AppSettings")
    OldSettings.objects.create(pk=1, fast_correction_enabled=False)

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewSettings = new_apps.get_model("monitor", "AppSettings")

    migrated = NewSettings.objects.get(pk=1)
    assert migrated.fast_correction_enabled is True
    assert migrated.fast_correction_rules == DEFAULT_RULES

    fresh = NewSettings.objects.create(pk=2)
    assert fresh.fast_correction_enabled is True
    assert fresh.fast_correction_rules == DEFAULT_RULES
