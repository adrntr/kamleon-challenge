import json
import logging
import typing
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text

from app.schemas.measurement import ITEM_PROBLEM_TYPES, ItemProblemType
from tests.support import request_during_uncommitted_insert

pytestmark = pytest.mark.anyio

PROBLEMS = "https://api.kamleon.example/problems/"
TIMESTAMP = "2026-10-06T09:00:00+02:00"
INSERT_MEASUREMENT = (
    "INSERT INTO measurements (id, device_id, device_measurement_id, type, unit, value, timestamp) "
    "VALUES (:id, :device_id, :device_measurement_id, :type, :unit, :value, :timestamp)"
)


async def create_device(client: AsyncClient) -> str:
    customer = await client.post(
        "/customers", json={"name": "Ana García", "email": "ana@kamleon.com"}
    )
    device = await client.post(
        f"/customers/{customer.json()['id']}/devices", json={"serial_number": "KM123"}
    )
    assert device.status_code == 201
    return device.json()["id"]


def measurement(**overrides: object) -> dict:
    return {
        "device_measurement_id": str(uuid.uuid4()),
        "type": "ph",
        "unit": "pH",
        "value": 7.41,
        "timestamp": TIMESTAMP,
    } | overrides


async def ingest(client: AsyncClient, device_id: str, *measurements: dict):
    return await client.post(
        f"/devices/{device_id}/measurements", json={"measurements": list(measurements)}
    )


def stored_rows(db: Engine) -> list:
    with db.connect() as connection:
        return connection.execute(
            text(
                "SELECT id, device_id, device_measurement_id, type, unit, value, timestamp "
                "FROM measurements"
            )
        ).all()


def stored_params(device_id: str, item: dict) -> dict:
    return {
        "id": uuid.uuid7(),
        "device_id": device_id,
        "device_measurement_id": item["device_measurement_id"],
        "type": item["type"],
        "unit": item["unit"],
        "value": item["value"],
        "timestamp": datetime.fromisoformat(item["timestamp"]),
    }


def summary(created: int = 0, duplicate: int = 0, rejected: int = 0) -> dict:
    return {
        "received": created + duplicate + rejected,
        "created": created,
        "duplicate": duplicate,
        "rejected": rejected,
    }


def batch_rejected(device_id: str, item: dict, problem: str, detail: str) -> dict:
    return {
        "type": PROBLEMS + "batch-rejected",
        "title": "All measurements in the batch were rejected",
        "status": 422,
        "detail": "1 of 1 measurements were rejected",
        "instance": f"/devices/{device_id}/measurements",
        "summary": summary(rejected=1),
        "results": [
            {
                "device_measurement_id": item["device_measurement_id"],
                "status": "rejected",
                "error": {"type": PROBLEMS + problem, "detail": detail},
            }
        ],
    }


async def test_ingest_creates_the_measurement(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    item = measurement()

    response = await ingest(client, device_id, item)

    assert response.status_code == 201
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "summary": summary(created=1),
        "results": [{"device_measurement_id": item["device_measurement_id"], "status": "created"}],
    }
    [row] = stored_rows(db)
    assert row.id.version == 7
    assert str(row.device_id) == device_id
    assert (str(row.device_measurement_id), row.type, row.unit, row.value) == (
        item["device_measurement_id"],
        "ph",
        "pH",
        7.41,
    )
    assert row.timestamp == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)


async def test_sequential_retry_is_duplicate(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    item = measurement()
    await ingest(client, device_id, item)

    response = await ingest(client, device_id, item)

    assert response.status_code == 201
    assert response.json() == {
        "summary": summary(duplicate=1),
        "results": [
            {"device_measurement_id": item["device_measurement_id"], "status": "duplicate"}
        ],
    }
    assert len(stored_rows(db)) == 1


@pytest.mark.parametrize("retry_timestamp", [TIMESTAMP, "2026-10-06T07:00:00Z"])
async def test_retry_with_offset_timestamp_is_duplicate(
    client: AsyncClient, db: Engine, retry_timestamp: str
) -> None:
    """The offset is not stored: the retry is compared as an instant, not as a string."""
    device_id = await create_device(client)
    item = measurement(timestamp=TIMESTAMP)
    await ingest(client, device_id, item)

    response = await ingest(client, device_id, item | {"timestamp": retry_timestamp})

    assert response.status_code == 201
    assert response.json()["results"][0]["status"] == "duplicate"
    assert len(stored_rows(db)) == 1


@pytest.mark.parametrize("value", [7.41, 0.1, 123456.789012345, 1e-7])
async def test_retry_of_value_not_exact_in_binary_is_duplicate(
    client: AsyncClient, db: Engine, value: float
) -> None:
    device_id = await create_device(client)
    item = measurement(value=value)
    await ingest(client, device_id, item)

    response = await ingest(client, device_id, item)

    assert response.status_code == 201
    assert response.json()["results"][0]["status"] == "duplicate"
    [row] = stored_rows(db)
    assert row.value == value


@pytest.mark.parametrize(
    "change",
    [
        {"value": 7.42},
        {"timestamp": "2026-10-06T09:01:00+02:00"},
        {"type": "temperature", "unit": "degC"},
    ],
    ids=["another-value", "another-instant", "another-registered-pair"],
)
async def test_same_id_with_another_payload_is_rejected(
    client: AsyncClient, db: Engine, change: dict
) -> None:
    device_id = await create_device(client)
    item = measurement(value=7.41)
    await ingest(client, device_id, item)
    [stored] = stored_rows(db)

    response = await ingest(client, device_id, item | change)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == batch_rejected(
        device_id,
        item,
        "measurement-id-conflict",
        f"Measurement {item['device_measurement_id']} is already stored with a different payload",
    )
    assert stored_rows(db) == [stored]


async def test_mixed_batch_returns_207_with_results_in_request_order(
    client: AsyncClient, db: Engine
) -> None:
    device_id = await create_device(client)
    valid = measurement()
    unknown = measurement(type="current", unit="MA")

    response = await ingest(client, device_id, valid, unknown)

    assert response.status_code == 207
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "summary": summary(created=1, rejected=1),
        "results": [
            {"device_measurement_id": valid["device_measurement_id"], "status": "created"},
            {
                "device_measurement_id": unknown["device_measurement_id"],
                "status": "rejected",
                "error": {
                    "type": PROBLEMS + "unknown-type-unit",
                    "detail": "Unit 'MA' is not registered for type 'current'",
                },
            },
        ],
    }
    [row] = stored_rows(db)
    assert str(row.device_measurement_id) == valid["device_measurement_id"]


async def test_unregistered_type_unit_pair_is_rejected(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    item = measurement(type="current", unit="MA")

    response = await ingest(client, device_id, item)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == batch_rejected(
        device_id, item, "unknown-type-unit", "Unit 'MA' is not registered for type 'current'"
    )
    assert stored_rows(db) == []


async def test_unknown_device_returns_404(client: AsyncClient, db: Engine) -> None:
    device_id = uuid.uuid4()

    response = await ingest(client, str(device_id), measurement())

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "device-not-found",
        "title": "Device not found",
        "status": 404,
        "detail": f"Device {device_id} does not exist",
        "instance": f"/devices/{device_id}/measurements",
    }
    assert stored_rows(db) == []


async def test_inactive_device_returns_409(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    await client.patch(f"/devices/{device_id}", json={"status": "inactive"})

    response = await ingest(client, device_id, measurement())

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "device-inactive",
        "title": "Device is inactive",
        "status": 409,
        "detail": f"Device {device_id} is inactive and cannot ingest measurements",
        "instance": f"/devices/{device_id}/measurements",
    }
    assert stored_rows(db) == []


@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        ({"measurements": []}, "#/measurements"),
        ({"measurements": [measurement() for _ in range(1001)]}, "#/measurements"),
        (
            {"measurements": [measurement(timestamp="2026-10-06T09:00:00")]},
            "#/measurements/0/timestamp",
        ),
        (
            {"measurements": [measurement(device_measurement_id="7")]},
            "#/measurements/0/device_measurement_id",
        ),
        ({"measurements": [measurement(unit="")]}, "#/measurements/0/unit"),
    ],
    ids=["empty", "too-many", "naive-timestamp", "malformed-id", "empty-unit"],
)
async def test_invalid_body_returns_422_and_stores_nothing(
    client: AsyncClient, db: Engine, body: dict, pointer: str
) -> None:
    device_id = await create_device(client)

    response = await client.post(f"/devices/{device_id}/measurements", json=body)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation-error"
    assert [error["pointer"] for error in response.json()["errors"]] == [pointer]
    assert stored_rows(db) == []


@pytest.mark.parametrize(
    "second_copy", [{}, {"value": 1.0}], ids=["identical-copy", "different-value"]
)
async def test_repeated_id_in_one_batch_returns_422(
    client: AsyncClient, db: Engine, second_copy: dict
) -> None:
    device_id = await create_device(client)
    item = measurement()

    response = await ingest(client, device_id, item, item | second_copy)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation-error"
    assert response.json()["errors"] == [
        {
            "pointer": "#/measurements/1/device_measurement_id",
            "detail": "device_measurement_id appears more than once in the batch",
        }
    ]
    assert stored_rows(db) == []


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
async def test_non_finite_value_returns_422(client: AsyncClient, db: Engine, token: str) -> None:
    """The JSON parser accepts these tokens; NaN != NaN would turn every retry into a conflict."""
    device_id = await create_device(client)
    body = json.dumps({"measurements": [measurement(value="VALUE")]})
    content = body.replace('"VALUE"', token)

    response = await client.post(
        f"/devices/{device_id}/measurements",
        content=content,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert [error["pointer"] for error in response.json()["errors"]] == ["#/measurements/0/value"]
    assert stored_rows(db) == []


async def test_malformed_device_id_returns_422(client: AsyncClient) -> None:
    response = await ingest(client, "nope", measurement())

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "device_id"


async def test_outcomes_are_logged_once_per_reason_without_values(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    device_id = await create_device(client)
    item = measurement(value=123.456)
    await ingest(client, device_id, item)
    caplog.clear()
    caplog.set_level(logging.INFO)

    await ingest(client, device_id, item)
    await ingest(client, device_id, item | {"value": 654.321})
    await ingest(client, device_id, measurement(type="current", unit="MA", value=987.654))

    service_records = [r for r in caplog.records if r.name == "app.services.measurements"]
    assert [(r.levelno, r.getMessage()) for r in service_records] == [
        (logging.INFO, f"device {device_id}: 1 measurement(s) duplicate (type=ph, unit=pH)"),
        (
            logging.WARNING,
            f"device {device_id}: 1 measurement(s) measurement-id-conflict (type=ph, unit=pH)",
        ),
        (
            logging.WARNING,
            f"device {device_id}: 1 measurement(s) unknown-type-unit (type=current, unit=MA)",
        ),
    ]
    for value in ("123.456", "654.321", "987.654"):
        assert value not in caplog.text


async def test_concurrent_retry_committed_with_same_payload_is_duplicate(
    client: AsyncClient, db: Engine
) -> None:
    """A inserts the measurement without committing, B retries it through the API, A commits."""
    device_id = await create_device(client)
    item = measurement()

    response = await request_during_uncommitted_insert(
        db,
        INSERT_MEASUREMENT,
        stored_params(device_id, item),
        ingest(client, device_id, item),
        commit=True,
    )

    assert response.status_code == 201
    assert response.json()["results"][0]["status"] == "duplicate"
    assert len(stored_rows(db)) == 1


async def test_concurrent_retry_rolled_back_is_created(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    item = measurement()

    response = await request_during_uncommitted_insert(
        db,
        INSERT_MEASUREMENT,
        stored_params(device_id, item),
        ingest(client, device_id, item),
        commit=False,
    )

    assert response.status_code == 201
    assert response.json()["results"][0]["status"] == "created"
    [row] = stored_rows(db)
    assert str(row.device_measurement_id) == item["device_measurement_id"]


async def test_concurrent_insert_committed_with_another_value_is_rejected(
    client: AsyncClient, db: Engine
) -> None:
    device_id = await create_device(client)
    item = measurement(value=7.41)

    response = await request_during_uncommitted_insert(
        db,
        INSERT_MEASUREMENT,
        stored_params(device_id, item) | {"value": 7.0},
        ingest(client, device_id, item),
        commit=True,
    )

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "batch-rejected"
    [result] = response.json()["results"]
    assert result["status"] == "rejected"
    assert result["error"]["type"] == PROBLEMS + "measurement-id-conflict"
    [row] = stored_rows(db)
    assert row.value == 7.0


def test_item_problem_types_match_the_documented_values() -> None:
    assert set(ITEM_PROBLEM_TYPES.values()) == set(typing.get_args(ItemProblemType))


async def test_device_deleted_during_ingestion_returns_404(client: AsyncClient, db: Engine) -> None:
    """A deletes the device without committing; B reads it as active, then its FK check blocks."""
    device_id = await create_device(client)

    response = await request_during_uncommitted_insert(
        db,
        "DELETE FROM devices WHERE id = :id",
        {"id": device_id},
        ingest(client, device_id, measurement()),
        commit=True,
    )

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "device-not-found"
    assert "measurements_device_id_fkey" not in response.text
    assert stored_rows(db) == []
