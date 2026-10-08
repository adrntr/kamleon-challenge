import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.anyio

PROBLEMS = "https://api.kamleon.example/problems/"
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://kamleon:kamleon@127.0.0.1:1/kamleon_test"


@pytest.fixture
async def unreachable_client():
    app = create_app(Settings(database_url=UNREACHABLE_DATABASE_URL))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await app.state.engine.dispose()


async def test_live_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_live_does_not_touch_the_database(unreachable_client: AsyncClient) -> None:
    response = await unreachable_client.get("/health/live")

    assert response.status_code == 200


async def test_ready_returns_ok_when_database_answers(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_503_problem_when_database_is_unreachable(
    unreachable_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        response = await unreachable_client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "service-unavailable",
        "title": "Service unavailable",
        "status": 503,
        "detail": "The database is not reachable",
        "instance": "/health/ready",
    }
    # Logged once, at warning, with the driver cause in one line and no stack trace.
    [record] = [r for r in caplog.records if r.name.startswith("app")]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "OperationalError" in record.getMessage()
    assert "\n" not in record.getMessage()
