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

### Errors

Every error response is an RFC 9457 problem details document (`application/problem+json`):

```bash
curl -i http://localhost:8000/does-not-exist
# 404 {"type":"https://api.kamleon.example/problems/not-found","title":"Not Found","status":404,"instance":"/does-not-exist"}
```

The full list of error cases is in *Error mapping* in `docs/DECISIONS.md`.
