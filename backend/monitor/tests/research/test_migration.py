from django.db import connection
from django.db.migrations.executor import MigrationExecutor
import pytest

@pytest.mark.django_db(transaction=True)
def test_research_migration_is_opt_in_and_does_not_invent_components():
    executor=MigrationExecutor(connection)
    previous=[('monitor','0049_traceable_billing_corrections')]
    try:
        executor.migrate(previous)
        apps=executor.loader.project_state(previous).apps
        apps.get_model('monitor','AppSettings').objects.create(pk=1,model_correction_enabled=False)
        executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
        from monitor.models import ResearchSettings,ResearchRequestComponents,AppSettings
        assert not ResearchSettings.load().enabled
        assert not ResearchSettings.load().projects
        assert not ResearchRequestComponents.objects.exists()
        assert not AppSettings.load().model_correction_enabled
    finally:
        executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
