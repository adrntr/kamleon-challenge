import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text

from tests.support import request_during_uncommitted_insert

pytestmark = pytest.mark.anyio

PROBLEMS = "https://api.kamleon.example/problems/"
ANA = {"name": "Ana García", "email": "ana@kamleon.com"}


async def test_create_customer_returns_201_with_location(client: AsyncClient, db: Engine) -> None:
    response = await client.post("/customers", json={**ANA, "email": "Ana@Kamleon.com"})

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/customers/{body['id']}"
    assert body["name"] == "Ana García"
    assert body["email"] == "ana@kamleon.com"
    assert body["created_at"].endswith(("Z", "+00:00"))
    with db.connect() as connection:
        emails = connection.execute(text("SELECT email FROM customers")).scalars().all()
    assert emails == ["ana@kamleon.com"]


async def test_openapi_declares_location_header_on_create_customer(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    created = schema["paths"]["/customers"]["post"]["responses"]["201"]
    assert created["headers"]["Location"]["schema"] == {"type": "string"}


async def test_get_customer_returns_what_was_created(client: AsyncClient) -> None:
    created = (await client.post("/customers", json=ANA)).json()

    response = await client.get(f"/customers/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_get_unknown_customer_returns_404_problem(client: AsyncClient) -> None:
    customer_id = uuid.uuid4()

    response = await client.get(f"/customers/{customer_id}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "customer-not-found",
        "title": "Customer not found",
        "status": 404,
        "detail": f"Customer {customer_id} does not exist",
        "instance": f"/customers/{customer_id}",
    }


async def test_get_malformed_customer_id_returns_422(client: AsyncClient) -> None:
    response = await client.get("/customers/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["errors"][0]["parameter"] == "customer_id"


async def test_duplicate_email_returns_409_regardless_of_case(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await client.post("/customers", json=ANA)

    with caplog.at_level(logging.INFO, logger="app"):
        response = await client.post("/customers", json={**ANA, "email": "ANA@kamleon.com"})

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": PROBLEMS + "email-already-registered",
        "title": "Email already registered",
        "status": 409,
        "detail": "A customer with email ana@kamleon.com is already registered",
        "instance": "/customers",
    }
    assert "customers_email_key" not in response.text
    [record] = [r for r in caplog.records if r.name.startswith("app")]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


async def test_duplicate_email_is_logged_without_the_email(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await client.post("/customers", json=ANA)

    with caplog.at_level(logging.INFO, logger="app"):
        response = await client.post("/customers", json=ANA)

    assert response.status_code == 409
    # The detail is for the client: the log line has only type, status, method and path.
    [record] = [r for r in caplog.records if r.name.startswith("app")]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "POST /customers -> 409 email-already-registered"
    assert "ana@kamleon.com" not in caplog.text.lower()


@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        ({"email": "ana@kamleon.com"}, "#/name"),
        ({"name": "Ana"}, "#/email"),
        ({**ANA, "name": "   "}, "#/name"),
        ({**ANA, "email": "not-an-email"}, "#/email"),
    ],
)
async def test_invalid_body_returns_422(client: AsyncClient, body: dict, pointer: str) -> None:
    response = await client.post("/customers", json=body)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation-error"
    assert [e["pointer"] for e in response.json()["errors"]] == [pointer]


async def test_registering_an_email_inserted_concurrently_and_committed_returns_409(
    client: AsyncClient, db: Engine
) -> None:
    """A inserts the email directly without committing, B registers it through the API."""
    response = await request_during_uncommitted_insert(
        db,
        "INSERT INTO customers (id, name, email) VALUES (:id, 'A', :email)",
        {"id": uuid.uuid4(), "email": ANA["email"]},
        client.post("/customers", json=ANA),
        commit=True,
    )

    assert response.status_code == 409
    assert response.json()["type"] == PROBLEMS + "email-already-registered"
    with db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM customers")).scalar_one() == 1
