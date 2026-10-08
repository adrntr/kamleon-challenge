"""Migrations go down and up cleanly, on a database of their own."""

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, make_url, text

from tests.support import alembic_config, create_database, sync_engine


@pytest.fixture
def migrations_url(database_url: str) -> str:
    url = make_url(database_url)
    name = url.database.removesuffix("_test") + "_migrations_test"
    url = url.set(database=name).render_as_string(hide_password=False)
    create_database(url)
    return url


def seeded_types(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT count(*) FROM measurement_types"))


def test_upgrade_and_downgrade_run_cleanly(migrations_url: str) -> None:
    config = alembic_config(migrations_url)
    engine = sync_engine(migrations_url)
    try:
        command.upgrade(config, "head")
        assert seeded_types(engine) == 6

        command.downgrade(config, "-1")
        assert seeded_types(engine) == 0

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")
        assert seeded_types(engine) == 6
    finally:
        engine.dispose()
