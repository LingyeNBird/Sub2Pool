import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


FROM = [("monitor", "0031_pooled_participant_contracts")]
TO = [("monitor", "0032_system_user_page_access")]
LEGACY_PAGES = {"participants", "particle_filter", "statistics"}


@pytest.mark.django_db(transaction=True)
def test_page_access_migration_preserves_regular_user_legacy_pages():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    User = old_apps.get_model("auth", "User")

    regular = User.objects.create(
        username="legacy-viewer",
        is_staff=False,
        is_superuser=False,
    )
    staff = User.objects.create(
        username="legacy-admin",
        is_staff=True,
        is_superuser=False,
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    PageAccess = new_apps.get_model("monitor", "SystemUserPageAccess")

    assert set(
        PageAccess.objects.filter(user_id=regular.id).values_list(
            "page_code",
            flat=True,
        )
    ) == LEGACY_PAGES
    assert not PageAccess.objects.filter(user_id=staff.id).exists()
