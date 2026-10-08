"""Database helpers shared by conftest and the migration tests."""

from alembic.config import Config
from psycopg.errors import DuplicateDatabase
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import ProgrammingError


def create_database(url: str) -> None:
    """Create the database if it does not exist, through the maintenance database.

    CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    database = make_url(url).database
    maintenance = create_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    except ProgrammingError as e:
        if not isinstance(e.orig, DuplicateDatabase):
            raise
    finally:
        maintenance.dispose()


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    return config


def sync_engine(url: str) -> Engine:
    return create_engine(url, connect_args={"options": "-c timezone=UTC"})
