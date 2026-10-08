import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text

from tests.support import request_during_uncommitted_insert

pytestmark = pytest.mark.anyio

PROBLEMS = "https://api.kamleon.example/problems/"
ANA = {"name": "Ana García", "email": "ana@kamleon.com"}


async def create_customer(client: AsyncClient, email: str = "ana@kamleon.com") -> str:
    return (await client.post("/customers", json={**ANA, "email": email})).json()["id"]


async def create_device(client: AsyncClient, customer_id: str, serial: str = "KM123") -> dict:
    response = await client.post(
        f"/customers/{customer_id}/devices", json={"serial_number": serial}
    )
    assert response.status_code == 201
    return response.json()


async def test_create_device_returns_201_active_with_location(client: AsyncClient) -> None:
    customer_id = await create_customer(client)

    response = await client.post(
        f"/customers/{customer_id}/devices", json={"serial_number": "  KM123 "}
    )

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/devices/{body['id']}"
    assert body["customer_id"] == customer_id
    assert body["serial_number"] == "KM123"
    assert body["status"] == "active"
    assert body["created_at"].endswith(("Z", "+00:00"))
    assert body["updated_at"].endswith(("Z", "+00:00"))


async def test_create_device_for_unknown_customer_returns_404(client: AsyncClient) -> None:
    customer_id = uuid.uuid4()

    response = await client.post(
        f"/customers/{customer_id}/devices", json={"serial_number": "KM123"}
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "customer-not-found",
        "title": "Customer not found",
        "status": 404,
        "detail": f"Customer {customer_id} does not exist",
        "instance": f"/customers/{customer_id}/devices",
    }
    assert "devices_customer_id_fkey" not in response.text


async def test_duplicate_serial_returns_409_even_for_another_customer(client: AsyncClient) -> None:
    await create_device(client, await create_customer(client))
    other_customer = await create_customer(client, "luis@kamleon.com")

    response = await client.post(
        f"/customers/{other_customer}/devices", json={"serial_number": "KM123"}
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "serial-already-registered",
        "title": "Serial number already registered",
        "status": 409,
        "detail": "A device with serial KM123 is already registered",
        "instance": f"/customers/{other_customer}/devices",
    }
    assert "devices_serial_number_key" not in response.text


@pytest.mark.parametrize(
    "body", [{}, {"serial_number": ""}, {"serial_number": "   "}, {"serial_number": 5}]
)
async def test_create_device_invalid_body_returns_422(client: AsyncClient, body: dict) -> None:
    customer_id = await create_customer(client)

    response = await client.post(f"/customers/{customer_id}/devices", json=body)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation-error"
    assert [e["pointer"] for e in response.json()["errors"]] == ["#/serial_number"]


async def test_create_device_malformed_customer_id_returns_422(client: AsyncClient) -> None:
    response = await client.post("/customers/nope/devices", json={"serial_number": "KM123"})

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "customer_id"


async def test_registering_a_serial_inserted_concurrently_and_committed_returns_409(
    client: AsyncClient, db: Engine
) -> None:
    """A inserts the serial directly without committing, B registers it through the API."""
    customer_id = await create_customer(client)

    response = await request_during_uncommitted_insert(
        db,
        "INSERT INTO devices (id, customer_id, serial_number, status) "
        "VALUES (:id, :customer_id, 'KM123', 'active')",
        {"id": uuid.uuid4(), "customer_id": customer_id},
        client.post(f"/customers/{customer_id}/devices", json={"serial_number": "KM123"}),
        commit=True,
    )

    assert response.status_code == 409
    assert response.json()["type"] == PROBLEMS + "serial-already-registered"
    with db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM devices")).scalar_one() == 1


async def test_registering_a_serial_inserted_concurrently_and_rolled_back_returns_201(
    client: AsyncClient, db: Engine
) -> None:
    customer_id = await create_customer(client)

    response = await request_during_uncommitted_insert(
        db,
        "INSERT INTO devices (id, customer_id, serial_number, status) "
        "VALUES (:id, :customer_id, 'KM123', 'active')",
        {"id": uuid.uuid4(), "customer_id": customer_id},
        client.post(f"/customers/{customer_id}/devices", json={"serial_number": "KM123"}),
        commit=False,
    )

    assert response.status_code == 201
    with db.connect() as connection:
        rows = connection.execute(text("SELECT id FROM devices")).scalars().all()
    assert [str(row) for row in rows] == [response.json()["id"]]


async def test_list_customer_devices_returns_only_that_customers_devices(
    client: AsyncClient,
) -> None:
    ana = await create_customer(client)
    luis = await create_customer(client, "luis@kamleon.com")
    first = await create_device(client, ana, "KM1")
    second = await create_device(client, ana, "KM2")
    await create_device(client, luis, "KM3")

    response = await client.get(f"/customers/{ana}/devices")

    assert response.status_code == 200
    assert response.json() == {"items": [first, second]}


async def test_list_devices_of_customer_without_devices_is_empty(client: AsyncClient) -> None:
    customer_id = await create_customer(client)

    response = await client.get(f"/customers/{customer_id}/devices")

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_list_devices_of_unknown_customer_returns_404(client: AsyncClient) -> None:
    customer_id = uuid.uuid4()

    response = await client.get(f"/customers/{customer_id}/devices")

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "customer-not-found"
    assert response.json()["instance"] == f"/customers/{customer_id}/devices"


async def test_list_devices_malformed_customer_id_returns_422(client: AsyncClient) -> None:
    response = await client.get("/customers/nope/devices")

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "customer_id"


async def test_get_device_returns_what_was_created(client: AsyncClient) -> None:
    device = await create_device(client, await create_customer(client))

    response = await client.get(f"/devices/{device['id']}")

    assert response.status_code == 200
    assert response.json() == device


async def test_get_unknown_device_returns_404_problem(client: AsyncClient) -> None:
    device_id = uuid.uuid4()

    response = await client.get(f"/devices/{device_id}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "device-not-found",
        "title": "Device not found",
        "status": 404,
        "detail": f"Device {device_id} does not exist",
        "instance": f"/devices/{device_id}",
    }


async def test_get_device_malformed_id_returns_422(client: AsyncClient) -> None:
    response = await client.get("/devices/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "device_id"


async def test_find_device_by_serial_returns_a_list_of_one(client: AsyncClient) -> None:
    customer_id = await create_customer(client)
    device = await create_device(client, customer_id, "KM123")
    await create_device(client, customer_id, "KM456")

    response = await client.get("/devices", params={"serial_number": "KM123"})

    assert response.status_code == 200
    assert response.json() == {"items": [device]}


async def test_find_device_by_unknown_serial_returns_empty_list(client: AsyncClient) -> None:
    response = await client.get("/devices", params={"serial_number": "NOPE"})

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_find_device_by_serial_strips_surrounding_whitespace(client: AsyncClient) -> None:
    device = await create_device(client, await create_customer(client), "KM123")

    response = await client.get("/devices", params={"serial_number": "  KM123 "})

    assert response.status_code == 200
    assert response.json() == {"items": [device]}


async def test_find_device_by_serial_is_case_sensitive(client: AsyncClient) -> None:
    await create_device(client, await create_customer(client), "KM123")

    response = await client.get("/devices", params={"serial_number": "km123"})

    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.parametrize("params", [{}, {"serial_number": ""}])
async def test_find_device_without_serial_returns_422(client: AsyncClient, params: dict) -> None:
    response = await client.get("/devices", params=params)

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "serial_number"
