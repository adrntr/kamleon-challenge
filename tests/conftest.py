from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, make_url, text

from app.config import Settings, require_psycopg_scheme
from app.main import create_app
from tests.support import alembic_config, create_database, sync_engine


class TestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: PostgresDsn

    _check_scheme = field_validator("test_database_url")(require_psycopg_scheme)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database, created if missing and migrated to head once per session."""
    url = str(TestSettings().test_database_url)
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.exit(
            f"TEST_DATABASE_URL must name a database ending in _test, got {database!r}: "
            "tests truncate tables",
            returncode=1,
        )
    create_database(url)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def db(database_url: str) -> Iterator[Engine]:
    """Sync engine on the test database, for setting up and inspecting rows."""
    engine = sync_engine(database_url)
    yield engine
    with engine.begin() as connection:
        # measurement_types is seed data owned by migrations: never truncated.
        connection.execute(text("TRUNCATE customers, devices, measurements"))
    engine.dispose()


@pytest.fixture
async def app(database_url: str, db: Engine) -> AsyncIterator[FastAPI]:
    app = create_app(Settings(database_url=database_url))
    yield app
    await app.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions stays on: an error escaping the app would fail the test.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
