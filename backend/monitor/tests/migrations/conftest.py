import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.fixture(autouse=True)
def restore_latest_schema_after_migration_test():
    """Migration tests may stop at historical states; restore runtime schema afterward."""
    yield
    call_command("flush", verbosity=0, interactive=False)
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
