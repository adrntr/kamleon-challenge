"""Problem-details handlers, exercised through routes that exist only in these tests."""

import logging

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import AwareDatetime, BaseModel

pytestmark = pytest.mark.anyio

PROBLEMS = "https://api.kamleon.example/problems/"
SECRET = "connection string postgresql://admin:hunter2@db"


class Reading(BaseModel):
    value: float
    timestamp: AwareDatetime


class Batch(BaseModel):
    measurements: list[Reading]


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/test/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET)

    @app.post("/test/batches")
    async def batches(batch: Batch, limit: int = 10) -> dict[str, int]:
        return {"received": len(batch.measurements)}

    return app


def app_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app")]


async def test_unexpected_error_returns_500_problem_without_internals(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = await client.get("/test/boom")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "internal-error",
        "title": "Internal server error",
        "status": 500,
        "detail": "An unexpected error occurred",
        "instance": "/test/boom",
    }
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    # Logged once, at error, with the stack trace.
    [record] = app_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


async def test_validation_error_points_to_the_invalid_body_field(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    body = {
        "measurements": [
            {"value": 7.41, "timestamp": "2026-10-06T09:00:00+02:00"},
            {"value": 98.76, "timestamp": "2026-10-06T09:00:00"},
        ]
    }

    response = await client.post("/test/batches", json=body)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "validation-error",
        "title": "Request validation failed",
        "status": 422,
        "instance": "/test/batches",
        "errors": [
            {"pointer": "#/measurements/1/timestamp", "detail": "Input should have timezone info"}
        ],
    }
    # The rejected input is neither returned nor logged: it can be health data.
    assert "2026-10-06T09:00:00" not in response.text
    [record] = app_records(caplog)
    assert record.levelno == logging.WARNING
    assert "98.76" not in caplog.text
    assert "2026-10-06T09:00:00" not in caplog.text


async def test_validation_error_names_the_invalid_query_parameter(client: AsyncClient) -> None:
    response = await client.post("/test/batches?limit=abc", json={"measurements": []})

    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "parameter": "limit",
            "detail": "Input should be a valid integer, unable to parse string as an integer",
        }
    ]


async def test_malformed_json_points_to_the_whole_body(client: AsyncClient) -> None:
    response = await client.post(
        "/test/batches", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["pointer"] == "#"


async def test_unknown_route_returns_404_problem(client: AsyncClient) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "not-found",
        "title": "Not Found",
        "status": 404,
        "instance": "/nope",
    }


async def test_wrong_method_returns_405_problem(client: AsyncClient) -> None:
    response = await client.post("/health/live")

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["allow"] == "GET"
    assert response.json() == {
        "type": PROBLEMS + "method-not-allowed",
        "title": "Method Not Allowed",
        "status": 405,
        "instance": "/health/live",
    }
