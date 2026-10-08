# AI log

How AI assistants were used in this project: what they proposed, what I changed or rejected, and how I validated it. Quick entries go in the table as they happen; significant cases are expanded below.

Tools: Claude (design discussion and Claude Code as coding agent), Gemini (second opinion on one concurrency question).

## Log

| What I asked                                      | What the AI proposed                                      | What I changed or rejected                                                                                      | How I validated it                                                                                        |
| ------------------------------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Close the race between ingestion and deactivation | `SELECT ... FOR SHARE` on the device row                  | Rejected: starvation risk and a lock on every ingestion for a millisecond window; no lock, documented guarantee | Checked Postgres sources (`README.tuplock`, pgsql-hackers thread) against a second assistant's FIFO claim |
| Duplicate device/customer registration            | Idempotent `201` for same-data retries                    | `409` always: a retry and a duplicate days later are indistinguishable                                          | Reasoned against the measurement case (firmware retries)                                                  |
| Registration conflict handling                    | `ON CONFLICT` for consistency with ingestion              | Catch `IntegrityError`, map by constraint name                                                                  | No follow-up query needed with `409`; FK violation needs catching anyway                                  |
| Customer deletion                                 | Design for device resale                                  | Kept simple model; resale documented as assumption and future improvement                                       | Requirements: a device belongs to a single customer, no transfers                                         |
| `AGENTS.md` integrity rule                        | "Integrity in the database, not the application"          | Split data invariants (constraints) from operation rules (services)                                             | Contradicted the inactive-device rule enforced by the application                                         |
| `AGENTS.md` uniqueness rule                       | List of today's two patterns                              | Criterion: duplicate as success → `ON CONFLICT`; as error → `IntegrityError`                                    | A list gives the agent nothing for new cases                                                              |
| `AGENTS.md` architecture                          | Router → service → repository, no domain layer            | Added `domain/` with pure rules; ORM coupling in services as documented trade-off                               | Inconsistent with the plan's own statement about the domain                                               |
| `AGENTS.md` tests and logging                     | One error test per endpoint; log events where they happen | One test per documented error + global handler test; "each fact logged once by the layer that resolves it"      | Questioned possible log duplication between service and handler                                           |
| Claude Code setup                                 | `CLAUDE.md` importing `@AGENTS.md`                        | Dropped `CLAUDE.md`; `AGENTS.md` loads natively                                                                 | Claude Code docs; `/memory`                                                                               |
| `add-endpoint` skill                              | Steps ending with the docs update                         | Contract check first; existing contracts need explicit approval to change                                       | Breaking changes to clients must never be implicit                                                        |

## Detailed entries

### Ingestion vs. device deactivation

**AI suggestion (Claude):** close the race between ingestion and device deactivation with `SELECT ... FOR SHARE` on the device row in every ingestion transaction. `FOR SHARE` is compatible with itself, so concurrent ingestions of the same device would not queue up, while the deactivation `UPDATE` would wait for in-flight ingestions to finish. This would guarantee that no measurement is accepted after the deactivation commit.

**What I changed:** I rejected the lock. Thinking through a continuous stream of measurements, I identified a starvation risk: if new `FOR SHARE` transactions keep arriving while the `UPDATE` waits, the deactivation might never get the lock. That makes it possible for a device to be impossible to deactivate under load, which is worse than the problem being solved. On top of that, every ingestion would pay for a lock to close a window of a few milliseconds. I chose no lock and documented the guarantee instead: deactivation applies to every measurement that arrives after it is committed (see *Concurrency* in `docs/DECISIONS.md`).

**How I validated it:** a second assistant (Gemini) claimed that Postgres queues lock requests FIFO, so starvation could not happen. I checked this against Postgres' own sources: the tuple-locking notes in the Postgres source (`README.tuplock`) acknowledge that a steady stream of share-lockers can block an exclusive locker, and a 2023 pgsql-hackers thread reproduces it: a new `FOR SHARE` is granted immediately while a `FOR UPDATE` is waiting. The FIFO queue it described applies to table-level locks, not row-level locks.

### Device and customer registration: idempotent success vs. `409`

**AI suggestion (Claude):** treat a duplicate registration with the same data (same serial and same customer, or same email and same name) as an idempotent retry and return `201` with the existing resource, consistent with measurement ingestion.

**What I changed:** I return `409` whenever the serial or email already exists. The server cannot tell a retry from a genuine duplicate registration days later, and answering "created" for a device created three days ago is misleading. Measurements need idempotent success because firmware retries automatically and must clear its buffer; registrations are occasional and done by people or applications, so a `409` followed by a lookup is enough.

### Registration conflicts: `ON CONFLICT` vs. catching `IntegrityError`

**AI suggestion (Claude):** use `INSERT ... ON CONFLICT DO NOTHING` for registrations too, for consistency with measurement ingestion.

**What I changed:** once registration always returns `409`, no follow-up query is needed after a conflict, so the main drawback of catching the exception disappeared. I also need to distinguish a duplicate value (`409`) from a missing customer (`404`, an FK violation that `ON CONFLICT` does not absorb anyway). I catch `IntegrityError` and map it by constraint name, and keep `ON CONFLICT` only for batch ingestion, where one duplicate must not abort the batch.

### Customer deletion: device resale

**AI suggestion (Claude):** while analyzing customer deletion, design for devices being resold to another customer (storing the owner at measurement time on every measurement, adjusting cascades accordingly).

**What I changed:** I noticed the requirements say a device belongs to a single customer and never mention transfers, and the scenario was adding complexity for a requirement that doesn't exist. I kept the simple model (`ON DELETE CASCADE`, measurements attached to the device) and documented "devices are not transferred" as an assumption, with owner-at-measurement-time as a future improvement.

### `AGENTS.md`: rule about data integrity

**AI suggestion (Claude):** a rule stating "integrity is enforced in the database, not in the application".

**What I changed:** it contradicted my own design, since "no measurements for inactive devices" is enforced by the application on purpose. I reworded it to separate **data invariants** (properties every stored row always satisfies: uniqueness, references, allowed values), which belong in database constraints, from **rules about operations** that depend on current state, which belong in services.

### `AGENTS.md`: uniqueness rule

**AI suggestion (Claude):** a rule listing the two patterns used today (`ON CONFLICT` for measurements, `IntegrityError` for registrations).

**What I changed:** a list of today's cases gives the agent nothing for a new case. I asked for the criterion instead: `ON CONFLICT` when a duplicate is an expected, successful outcome; catching `IntegrityError` when a duplicate is an error for the client; ask if neither fits.

### `AGENTS.md`: architecture without a domain layer

**AI suggestion (Claude):** an architecture section with router → service → repository only, even though the plan had said the domain should not depend on FastAPI or SQLAlchemy.

**What I changed:** I flagged the missing domain layer and questioned whether services using ORM models couple business rules to the ORM. Result: business rules are pure functions in `domain/` that receive plain values and are testable without a database; services orchestrate with ORM models as a documented trade-off, with the evolution path to full domain entities described in `docs/DECISIONS.md`.

### `AGENTS.md`: tests and logging rules

**AI suggestion (Claude):** "every endpoint has a success test and at least one error test", and "business events are logged where they happen".

**What I changed:**
- **Tests:** one test per documented error case (driven by the *Error mapping* table), plus a single test of the global exception handler checking that a `500` is returned as problem details without leaking internals.
- **Logging:** I questioned whether logging business events in the service would duplicate logs already written by the handler. The rule was clarified to "each fact is logged once, by the layer that resolves it": errors only by the global exception handler, non-error outcomes (e.g. a duplicate measurement, which raises no exception) only by the service.

### Claude Code setup: `CLAUDE.md`

**AI suggestion (Claude):** create a `CLAUDE.md` importing `@AGENTS.md`, because Claude Code would not read `AGENTS.md` otherwise.

**What I changed and how I validated it:** while reading the Claude Code documentation for this setup, the docs showed that current Claude Code reads `AGENTS.md` natively when no `CLAUDE.md` exists, and that creating a `CLAUDE.md` without the import would make it ignore `AGENTS.md`. I dropped `CLAUDE.md` and verified with `/memory` that `AGENTS.md` loads.

### `add-endpoint` skill: changing existing contracts

**AI suggestion (Claude):** a skill whose steps went from schemas to code and ended by updating the API table in `docs/DECISIONS.md`, with a description saying it could "add or change" endpoints.

**What I changed:** the contract check moved to the first step, so the agent implements what I decided instead of documenting what it decided. I also asked whether the skill allowed the agent to change an existing endpoint's contract on its own. Changing routes, fields, status codes or problem types is a breaking change for clients (apps and device firmware), so the skill now requires the agent to describe exactly what would change and which clients could break, and to wait for explicit approval; changes that keep the contract intact need no approval.