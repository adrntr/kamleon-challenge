# Kamleon devices API

REST API (FastAPI + PostgreSQL) to manage customers, their connected devices and the measurements those devices send. Design decisions and their trade-offs are in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Running

```bash
docker compose up --build
```

This starts three services:

- `db`: PostgreSQL 18, published on host port 5432.
- `migrate`: runs `alembic upgrade head` once and exits.
- `api`: starts only after `migrate` succeeds, and listens on http://localhost:8000.

If host port 5432 is already taken, publish Postgres on another port, for example `DB_HOST_PORT=5433 docker compose up --build`, and use that port in the URLs below.

## Tests

The tests run against a real PostgreSQL database, the `db` service, from the host. They need [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env    # DATABASE_URL and TEST_DATABASE_URL for the host
docker compose up -d db
uv run pytest
```

The tests create the `kamleon_test` database if it is missing and migrate it to head. They refuse to run against a database whose name does not end in `_test`, because they truncate tables.

Lint and format: `uv run ruff check` and `uv run ruff format --check`.

## Migrations

```bash
uv run alembic upgrade head      # apply
uv run alembic downgrade -1      # revert the last one
```

Alembic reads the database URL from `DATABASE_URL`, the same setting the API uses. It only accepts the `postgresql+psycopg://` scheme.

## Endpoints

### Health

Liveness: the process responds. It never touches the database.

```bash
curl -i http://localhost:8000/health/live
# 200 {"status":"ok"}
```

Readiness: the database answers.

```bash
curl -i http://localhost:8000/health/ready
# 200 {"status":"ok"}
# 503 application/problem+json if the database is unreachable
```

### Customers

Register a customer. The email is lowercased before storing.

```bash
curl -i -X POST http://localhost:8000/customers \
  -H 'Content-Type: application/json' \
  -d '{"name": "Ana García", "email": "ana@kamleon.com"}'
# 201 + Location: /customers/{id}
# 409 email-already-registered, 422 validation-error
```

Retrieve a customer.

```bash
curl -i http://localhost:8000/customers/{customer_id}
# 200
# 404 customer-not-found
```

### Devices

Register a device for a customer. It starts as `active`.

```bash
curl -i -X POST http://localhost:8000/customers/{customer_id}/devices \
  -H 'Content-Type: application/json' \
  -d '{"serial_number": "KM123"}'
# 201 + Location: /devices/{id}
# 404 customer-not-found, 409 serial-already-registered, 422 validation-error
```

List the devices of a customer.

```bash
curl -i http://localhost:8000/customers/{customer_id}/devices
# 200 {"items": [...]}
# 404 customer-not-found
```

Retrieve a device by id.

```bash
curl -i http://localhost:8000/devices/{device_id}
# 200
# 404 device-not-found
```

Find a device by serial number (the list is empty if none matches).

```bash
curl -i 'http://localhost:8000/devices?serial_number=KM123'
# 200 {"items": [...]}
# 422 validation-error if serial_number is missing or empty
```

### Errors

Every error response is an RFC 9457 problem details document (`application/problem+json`):

```bash
curl -i http://localhost:8000/does-not-exist
# 404 {"type":"https://api.kamleon.example/problems/not-found","title":"Not Found","status":404,"instance":"/does-not-exist"}
```

The full list of error cases is in *Error mapping* in `docs/DECISIONS.md`.
