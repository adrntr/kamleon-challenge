---
name: add-endpoint
description: Add or change a REST endpoint end to end (contract, schemas, repository, service, router, tests, docs). Use when the user asks to create a new endpoint or modify the request, response or errors of an existing one.
---

# Add an endpoint

Endpoint to add or change: $ARGUMENTS

Follow these steps in order. Apply the rules in `AGENTS.md` throughout; do not restate or override them.

1. **Contract first.** Check `docs/DECISIONS.md` (*API contract*, *Error mapping*).
   - **New endpoint not defined there:** propose its row (method, route, body, responses) and its error cases, and wait for confirmation before writing code.
   - **Existing endpoint:** its contract (route, request and response fields, status codes, problem types) is a promise to clients. Changing it is a breaking change: describe exactly what would change and which clients could break, and do not implement it without explicit approval. Changes that keep the contract intact (internal refactors, new tests) need no approval.
   - Never invent status codes or problem types that contradict the documented ones.
2. **Schemas** (`schemas/`): one input schema and one output schema. The output schema uses `from_attributes=True`. Timestamps are `AwareDatetime`.
3. **Domain** (`domain/`): add any new domain errors and pure rules the use case needs. No FastAPI or SQLAlchemy imports.
4. **Repository** (`repositories/`): database access only. Translate integrity errors into domain errors by constraint name, keeping the cause (`raise ... from e`). If the schema changes, create a new Alembic migration and review it by hand.
5. **Service** (`services/`): the use case. Owns the transaction, calls domain rules, raises domain errors. Never imports from `schemas/` and never raises `HTTPException`.
6. **Router** (`api/`): converts the input schema into plain arguments, calls one service function, converts the result into the output schema. Map any new domain error in the global exception handler to its documented status and problem type.
The problem `detail` is written for clients from domain data (e.g. "A device with serial KM123 is already registered"). Never include `str()` of the exception or of its cause: driver and database messages expose implementation details (table, column and constraint names, SQL fragments, library versions) that help an attacker map the system and couple clients to internals. The full cause chain goes only to the logs.
7. **Tests**: one success test and one test per documented error case, against real PostgreSQL.
   **Concurrency:** if two concurrent requests to this endpoint could conflict, identify the interleaving that produces the conflict and force it deterministically with separate database connections and controlled commits, instead of firing parallel requests and hoping they overlap. Most common case here, an insert into a table with a unique constraint: A inserts without committing, B attempts the same insert and blocks, A commits, assert how B's conflict is handled (never a `500`). Read-only endpoints and idempotent "set to X" updates need no concurrency test; if the endpoint has a race that `docs/DECISIONS.md` explicitly accepts, do not test it as a bug.
8. **Docs**:
   - **OpenAPI** is the field-level reference and is generated from code: give schema fields `description` and examples, set `response_model` and `status_code`. Declare in the route's `responses=` every status listed for the endpoint in `docs/DECISIONS.md` (the *Responses* column of *API contract* and the *Error mapping* table): error statuses with the problem details schema, and additional success statuses (e.g. `207` for partial batches) with their own schema. Per-item outcomes that are not HTTP statuses (e.g. batch `results[].error.type` values) are documented as the possible values of that schema field. Do not document fields by hand anywhere else.
   - **`README.md`**: add one `curl` example for the endpoint.
   - **`docs/DECISIONS.md`**: make sure the endpoint's row and its error cases match what was implemented.
9. **Verify**: run the full *Definition of done* from `AGENTS.md` before reporting the task as done.