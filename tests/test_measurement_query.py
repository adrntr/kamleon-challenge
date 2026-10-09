import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import Engine

from app.api.dependencies import get_now
from tests.test_measurements import PROBLEMS, create_device, measurement, store

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def fixed_now(app: FastAPI) -> Iterator[datetime]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield NOW
    app.dependency_overrides.clear()


def store_at(db: Engine, device_id: str, timestamp: datetime, **fields: object) -> dict:
    item = measurement(timestamp=timestamp.isoformat(), **fields)
    store(db, device_id, item)
    return item


async def query(client: AsyncClient, device_id: str, **params: object):
    return await client.get(f"/devices/{device_id}/measurements", params=params)


def timestamps(response) -> list[datetime]:
    return [datetime.fromisoformat(item["timestamp"]) for item in response.json()["items"]]


async def test_returns_measurements_in_range_oldest_first(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    later = store_at(db, device_id, NOW - timedelta(minutes=1), type="temperature", unit="degC")
    earlier = store_at(db, device_id, NOW - timedelta(minutes=2), value=7.38)

    response = await query(
        client, device_id, **{"from": (NOW - timedelta(hours=1)).isoformat(), "to": NOW.isoformat()}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is False
    assert [item["device_measurement_id"] for item in body["items"]] == [
        earlier["device_measurement_id"],
        later["device_measurement_id"],
    ]
    first = body["items"][0]
    uuid.UUID(first["id"])
    assert first | {"id": None} == {
        "id": None,
        "device_measurement_id": earlier["device_measurement_id"],
        "type": "ph",
        "unit": "pH",
        "value": 7.38,
        "timestamp": "2026-10-07T11:58:00Z",
    }


async def test_filters_by_type(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    ph = store_at(db, device_id, NOW - timedelta(minutes=2))
    store_at(db, device_id, NOW - timedelta(minutes=1), type="temperature", unit="degC")

    response = await query(
        client,
        device_id,
        type="ph",
        **{"from": (NOW - timedelta(hours=1)).isoformat(), "to": NOW.isoformat()},
    )

    assert response.status_code == 200
    assert [item["device_measurement_id"] for item in response.json()["items"]] == [
        ph["device_measurement_id"]
    ]


async def test_does_not_return_other_devices_measurements(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    customer = await client.post("/customers", json={"name": "Bea", "email": "bea@kamleon.com"})
    other = await client.post(
        f"/customers/{customer.json()['id']}/devices", json={"serial_number": "KM456"}
    )
    store_at(db, other.json()["id"], NOW - timedelta(minutes=1))

    response = await query(client, device_id, to=NOW.isoformat())

    assert response.json() == {"items": [], "has_more": False}


async def test_range_includes_from_and_excludes_to(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    start, end = NOW - timedelta(hours=1), NOW
    microsecond = timedelta(microseconds=1)
    for timestamp in (start - microsecond, start, end - microsecond, end):
        store_at(db, device_id, timestamp)

    response = await query(client, device_id, **{"from": start.isoformat(), "to": end.isoformat()})

    assert timestamps(response) == [start, end - microsecond]


async def test_range_offsets_are_converted_not_ignored(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    store_at(db, device_id, datetime(2026, 10, 7, 7, 30, tzinfo=UTC))

    # [09:00+02:00, 10:00+02:00) is [07:00Z, 08:00Z).
    response = await query(
        client,
        device_id,
        **{"from": "2026-10-07T09:00:00+02:00", "to": "2026-10-07T10:00:00+02:00"},
    )

    assert timestamps(response) == [datetime(2026, 10, 7, 7, 30, tzinfo=UTC)]


async def test_default_range_is_the_last_24_hours(
    client: AsyncClient, db: Engine, fixed_now: datetime
) -> None:
    device_id = await create_device(client)
    day_ago = fixed_now - timedelta(hours=24)
    microsecond = timedelta(microseconds=1)
    for timestamp in (day_ago - microsecond, day_ago, fixed_now - microsecond, fixed_now):
        store_at(db, device_id, timestamp)

    response = await query(client, device_id)

    assert response.status_code == 200
    assert timestamps(response) == [day_ago, fixed_now - microsecond]


async def test_default_from_is_24_hours_before_given_to(client: AsyncClient, db: Engine) -> None:
    device_id = await create_device(client)
    to = datetime(2026, 1, 1, tzinfo=UTC)
    for timestamp in (to - timedelta(hours=25), to - timedelta(hours=24), to):
        store_at(db, device_id, timestamp)

    response = await query(client, device_id, to=to.isoformat())

    assert timestamps(response) == [to - timedelta(hours=24)]


async def test_default_to_is_now(client: AsyncClient, db: Engine, fixed_now: datetime) -> None:
    device_id = await create_device(client)
    start = fixed_now - timedelta(days=3)
    for timestamp in (start, fixed_now - timedelta(seconds=1), fixed_now):
        store_at(db, device_id, timestamp)

    response = await query(client, device_id, **{"from": start.isoformat()})

    assert timestamps(response) == [start, fixed_now - timedelta(seconds=1)]


@pytest.mark.parametrize(("stored", "has_more"), [(3, True), (2, False)])
async def test_has_more_flags_truncation(
    client: AsyncClient, db: Engine, fixed_now: datetime, stored: int, has_more: bool
) -> None:
    device_id = await create_device(client)
    for minutes in range(stored, 0, -1):
        store_at(db, device_id, fixed_now - timedelta(minutes=minutes))

    response = await query(client, device_id, limit=2)

    assert response.status_code == 200
    assert response.json()["has_more"] is has_more
    # The first `limit` rows of the range, oldest first.
    assert timestamps(response) == [
        fixed_now - timedelta(minutes=minutes) for minutes in range(stored, stored - 2, -1)
    ]


def assert_validation_error(response, parameter: str) -> None:
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == PROBLEMS + "validation-error"
    assert [error["parameter"] for error in body["errors"]] == [parameter]


@pytest.mark.parametrize("from_offset", [timedelta(0), timedelta(minutes=1)])
async def test_from_not_earlier_than_to_returns_422(
    client: AsyncClient, from_offset: timedelta
) -> None:
    device_id = await create_device(client)
    to = datetime(2026, 1, 1, tzinfo=UTC)

    response = await query(
        client, device_id, **{"from": (to + from_offset).isoformat(), "to": to.isoformat()}
    )

    assert_validation_error(response, "from")
    assert response.json()["errors"][0]["detail"] == "from must be earlier than to"


async def test_from_not_earlier_than_default_to_returns_422(
    client: AsyncClient, fixed_now: datetime
) -> None:
    device_id = await create_device(client)

    response = await query(client, device_id, **{"from": fixed_now.isoformat()})

    assert_validation_error(response, "from")


@pytest.mark.parametrize("parameter", ["from", "to"])
async def test_naive_datetime_returns_422(client: AsyncClient, parameter: str) -> None:
    device_id = await create_device(client)

    response = await query(client, device_id, **{parameter: "2026-10-07T12:00:00"})

    assert_validation_error(response, parameter)


@pytest.mark.parametrize("limit", [0, 5001])
async def test_limit_out_of_bounds_returns_422(client: AsyncClient, limit: int) -> None:
    device_id = await create_device(client)

    response = await query(client, device_id, limit=limit)

    assert_validation_error(response, "limit")


async def test_limit_of_5000_is_accepted(client: AsyncClient) -> None:
    device_id = await create_device(client)

    response = await query(client, device_id, limit=5000)

    assert response.status_code == 200


async def test_unknown_device_returns_404(client: AsyncClient) -> None:
    device_id = uuid.uuid4()

    response = await query(client, str(device_id))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "device-not-found",
        "title": "Device not found",
        "status": 404,
        "detail": f"Device {device_id} does not exist",
        "instance": f"/devices/{device_id}/measurements",
    }


async def test_malformed_device_id_returns_422(client: AsyncClient) -> None:
    response = await query(client, "not-a-uuid")

    assert_validation_error(response, "device_id")
