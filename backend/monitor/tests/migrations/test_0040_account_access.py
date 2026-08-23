import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


FROM = [("monitor", "0039_applied_projection_sources")]
TO = [("monitor", "0040_monitoredaccount_authorized_users")]


@pytest.mark.django_db(transaction=True)
def test_account_access_migration_preserves_existing_page_visibility():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    user_app, user_model = settings.AUTH_USER_MODEL.split(".")
    User = old_apps.get_model(user_app, user_model)
    MonitoredAccount = old_apps.get_model("monitor", "MonitoredAccount")
    PageAccess = old_apps.get_model("monitor", "SystemUserPageAccess")
    QuotaPool = old_apps.get_model("monitor", "QuotaPool")

    account_viewer = User.objects.create(username="account-viewer")
    other_viewer = User.objects.create(username="other-viewer")
    staff = User.objects.create(username="staff", is_staff=True)
    pool = QuotaPool.objects.create(name="迁移测试池")
    first = MonitoredAccount.objects.create(
        pool_id=pool.id,
        external_account_id=71,
        name="主账号",
    )
    second = MonitoredAccount.objects.create(
        pool_id=pool.id,
        external_account_id=72,
        name="备用账号",
    )
    PageAccess.objects.create(
        user_id=account_viewer.id,
        page_code="account_status",
    )
    PageAccess.objects.create(user_id=other_viewer.id, page_code="tutorial")
    PageAccess.objects.create(user_id=staff.id, page_code="account_status")

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewAccount = new_apps.get_model("monitor", "MonitoredAccount")

    assert set(
        NewAccount.objects.get(pk=first.id).authorized_users.values_list(
            "id",
            flat=True,
        )
    ) == {account_viewer.id}
    assert set(
        NewAccount.objects.get(pk=second.id).authorized_users.values_list(
            "id",
            flat=True,
        )
    ) == {account_viewer.id}
