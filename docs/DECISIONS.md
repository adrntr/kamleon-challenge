# Design decisions
Each decision lists the rejected alternatives and the reason.

## Data model

### Identifiers

**customers.id — UUIDv4**
- **Rejected alternative:** auto-increment integer (`bigint identity`).
- **Why:** a sequential id in URLs lets anyone guess other customers' ids and reveals how many customers we have. This is defense in depth: what actually protects access to a customer is authorization, not how hard the id is to guess. At the expected volume (thousands of rows) the whole index fits in memory, so UUID randomness has no performance impact.
- **Rejected alternative:** uuidv7: provides information about the date of creation of the customer and it is not needed based on the expected quantity of users.

**devices.id — UUIDv4 (surrogate key); `serial_number` as `UNIQUE NOT NULL`**
- **Rejected alternative:** `serial_number` as primary key (natural key). 
- **Why:** the serial is entered by a human or by firmware, so it can be wrong or change. If it were the PK, fixing it would mean updating the FK on every measurement of that device. As a surrogate key, the serial is fixed in a single row and its uniqueness is still enforced by the database.
- **Examples:** a technician replaces the main board and the device now reports a different serial, but the customer's history must stay attached to the same device; or the manufacturer changes the serial format (`KM12345` → `KAM-2026-12345`). In both cases only one column of one row changes, and no foreign key is affected.
- **Rejected alternative**: uuidv7: provides information about the date of creation of the device and it is not needed based on the expected quantity of devices.


**measurements.id — UUIDv7**
- **Rejected alternative:** UUIDv4 (random).
- **Why:** measurements is the highest-volume table and receives continuous inserts. UUIDv7 grows with time, so every insert lands on the last leaf of the PK index: that page is always in memory and fills almost completely before splitting. With UUIDv4 each insert lands on a random leaf. Once the index no longer fits in RAM, that means a random disk read per insert. Page splits spread across the whole tree also leave pages half full, so the index bloats and fits in memory even worse.
- **Note:** queries order and filter by `timestamp` (when the device generated the measurement), not by id. UUIDv7 is chosen for index insert behavior, not for ordering.
- **Privacy consideration:** a UUIDv7 embeds its creation time (first 48 bits, millisecond precision). Measurements are health-related data, so a measurement id appearing next to a `device_id` (e.g. in a URL written to access logs) reveals that a specific person took a measurement at an exact time, without exposing the value. This was evaluated and accepted: the request log line already carries its own timestamp and the `device_id` already identifies the person, so UUIDv4 would not remove the exposure. The actual risk is personal identifiers in logs, which is addressed by log hygiene (see README, GDPR section), not by the id type.

### Timestamps

**All timestamp columns (`created_at`, `updated_at`, `measurements.timestamp`) — `timestamptz`**
- **Rejected alternative:** `timestamp` (without time zone).
- **Why:** devices are deployed in different countries and report local times. `timestamp` stores the wall-clock value as-is, without knowing which zone it belongs to, so two measurements taken at the same instant in Barcelona (09:00) and Mexico City (01:00) are stored as different times and time-range queries return wrong results. `timestamptz` uses the offset in the input to convert the value to UTC and stores a single absolute instant; the original offset is not kept. The database session time zone is set to UTC, so values are also returned in UTC.
- **Input validation:** measurement timestamps must include a UTC offset. The API uses pydantic's `AwareDatetime` and rejects naive datetimes with a 422 instead of guessing the time zone.


### Device status

**`devices.status` — `text NOT NULL` with `CONSTRAINT devices_status_check CHECK (status IN ('active', 'inactive'))`**
- **States:** `active` and `inactive`. These are the only states the requirements need; more are not added to avoid unnecessary complexity.
- **Rejected alternative: boolean `is_active`.** A `status` column allows adding new states later without changing the column type or migrating existing data.
- **Rejected alternative: Postgres `ENUM`.** Renaming a value is easy (`ALTER TYPE ... RENAME VALUE`), but there is no way to remove one: retiring a state requires creating a new type and changing the column type, which rewrites the whole table under an exclusive lock. Since ingestion reads the device status before every insert, this would block ingestion for the duration of the rewrite. With `CHECK`, a state can be renamed or retired with a batched `UPDATE` plus a new constraint added as `NOT VALID` (only a brief lock, no table scan) and then validated with `VALIDATE CONSTRAINT`, which does not block reads or writes.
- **Rejected alternative: lookup table (`device_statuses` with FK).** It allows adding states without a migration, but that is not a real benefit: a new state still requires code changes and a deployment to be handled correctly. It adds a table without adding safety.
- **Rejected alternative: free `text` without constraint.** No integrity guarantee. A typo such as `'Active'` would make ingestion silently reject every measurement for that device until someone noticed.

### Customer email

**`customers.email` — `text NOT NULL UNIQUE` with `CHECK (email = lower(email))`; the application lowercases emails before storing and before lookups**

```sql
email text NOT NULL,
CONSTRAINT customers_email_key UNIQUE (email),
CONSTRAINT customers_email_lowercase_check CHECK (email = lower(email))
```

Lookups compare against the lowercased input, so the `UNIQUE` index is used:

```sql
WHERE email = lower($1)
```

- **Assumption:** emails are treated as case-insensitive, so `Ana@kamleon.com` and `ana@kamleon.com` are the same customer. Strictly, the email standard allows the local part (before `@`) to be case-sensitive, but in practice providers do not enforce it, so case-insensitive handling is the practical choice.
- **Normalize, don't reject:** input like `Ana@kamleon.com` is converted to lowercase, not rejected with an error.
- **Rejected alternative: application-only normalization.** Any other writer (a manual `INSERT`, a future endpoint) could store uppercase emails and create duplicates. The `CHECK` makes the database enforce it regardless of the writer.
- **Rejected alternative: unique index on `lower(email)`, keeping the original casing.** It enforces uniqueness, but every query must remember to use `lower(email)`; forgetting it does not fail, it just doesn't find the customer.
- **Rejected alternative: `citext`.** Postgres-specific extension that hides case-insensitive comparison inside the column type: a reader of `WHERE email = $1` cannot tell the comparison ignores case. Explicit lowercasing was preferred.
- **Trade-off accepted:** the original casing typed by the customer is not preserved.

### Measurement value

**`measurements.value` — `double precision`**
- **Rejected alternative: `NUMERIC`.** Exact decimal storage is needed for money, not for sensor readings: a sensor value already carries its own measurement error, so exact decimal representation adds nothing. `NUMERIC` is variable-length and its arithmetic is done in software, which is slower for aggregates (averages, max/min) over millions of rows. `double precision` is a fixed 8 bytes and uses native hardware arithmetic.
- **Rejected alternative: `real` (4 bytes).** About 6-7 significant digits is enough for most raw sensor readings, but not for accumulated values (e.g. total volume over years) or for aggregates: sums over millions of rows accumulate rounding error, and with `real` it becomes visible much earlier. The limit is precision, not range: both types have a huge maximum value, but `real` cannot distinguish 100,000,000 from 100,000,001. Saving 4 bytes per row is marginal compared to the rest of the row (UUIDs, timestamp, type, unit and Postgres row overhead).


### Measurement types and units

**Lookup table `measurement_types (type, unit)` with `PRIMARY KEY (type, unit)`; `measurements (type, unit)` references it with a composite FK. Both columns are `NOT NULL`.**

```sql
CREATE TABLE measurement_types (
    type text NOT NULL,
    unit text NOT NULL,
    PRIMARY KEY (type, unit)
);

-- in measurements:
type text NOT NULL,
unit text NOT NULL,
CONSTRAINT measurements_type_unit_fkey
    FOREIGN KEY (type, unit) REFERENCES measurement_types (type, unit)
```

- **What it guarantees:** a measurement can only be stored with a registered `(type, unit)` pair, so `('ph', 'mA')` is rejected even if `ph` and `mA` exist separately. The check happens only on insert/update: reads and filters by `type` need no join, since `type` and `unit` are stored as text in `measurements`.
- **Several units per type are allowed.** This makes unit transitions possible without losing data (e.g. `current` from `mA` to `µA`):
  1. a migration adds `('current', 'µA')`;
  2. devices are updated gradually while both units coexist;
  3. history is converted in batches; each row's `unit` says whether it is already converted;
  4. `('current', 'mA')` is removed. The FK prevents removing it while any measurement still uses it.
- **Dimensionless measurements** (pH, percentages) use a label as unit (`'pH'`, `'%'`), never `NULL`: with the default `MATCH SIMPLE`, a composite FK is not checked at all if any of its columns is `NULL`, so a `NULL` unit would let any `type` through.
- **Seeding:** the initial types are inserted by an Alembic data migration, so the database is ready after `alembic upgrade head`. The type list is an assumption, since the requirements don't define it. Adding a new sensor type is a new migration with an `INSERT`, with no application code changes.
- **Ingestion contract:** devices must send a registered `(type, unit)` pair; anything else is rejected. **Known limitation:** measurements with an unregistered unit are lost until the firmware is fixed or the pair is registered.
- **Reads:** measurements are returned as stored, each with its own `unit`, so a value is never ambiguous. During a unit transition, a query filtered only by `type` can return mixed units. **Future improvement:** convert to a unit requested by the client.

- **Rejected alternative: `CHECK (unit IN (...))`.** It validates the unit alone, not whether it matches the type.
- **Rejected alternative: one unit per type (`PRIMARY KEY (type)`).** Simpler, but any unit change becomes impossible without rejecting the devices that have not been updated yet.
- **Rejected alternative: validation only in application code (a `type → unit` map).** A new sensor type would require a code change and a deployment, and writers outside the API would not be validated.
- **Rejected alternative: accepting any unit and converting on ingestion.** It adds conversion logic and its bugs to the API. A strict contract was preferred, with unit transitions handled by registering new pairs.
- **Rejected alternative: lowercasing units, as done for emails.** Units are case-sensitive: `mA` is milliampere, `MA` is megaampere.
- **Future improvement:** an authenticated admin endpoint to register measurement types without a migration.

----

## Business rules: who enforces them

| Rule                                                       | Enforced by | Mechanism                                                                                                                                              |
| ---------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| (a) A device's serial number is unique                     | Database    | `UNIQUE (serial_number)` on `devices`                                                                                                                  |
| (b) A device belongs to a single customer                  | Database    | `devices.customer_id` is a single `NOT NULL` column with an FK to `customers (id)`                                                                     |
| (c) Measurements are only associated with existing devices | Database    | `measurements.device_id` is `NOT NULL` with an FK to `devices (id)`                                                                                    |
| (d) Measurements cannot be ingested for inactive devices   | Application | Within the ingestion transaction: read the device status and reject if it is not `active`. No row lock; see *Concurrency* for the accepted race window |

- **(a)–(c) are declarative constraints:** the rule is declared once and the database enforces it on every write, whatever the writer (API, manual `INSERT`, another service).
- **(b):** a single `customer_id` column means a device can only have one owner; several owners would require a join table. `NOT NULL` prevents a device without owner, since an FK does not check `NULL` values. **Assumption:** the requirements do not contemplate unassigned devices (e.g. stock not yet sold); supporting them would need an explicit model change.
- **(d) cannot be declarative:** a `CHECK` only sees the row being written, and an FK only checks that the referenced row exists, not its column values. The rule depends on the current value of a column in another table at insert time.
- **Rejected alternative for (d): a trigger.** It would enforce the rule inside the database for every writer, but it hides business logic where developers don't look for it. The rule is kept in the application service, which is also where the API returns a meaningful error.

--- 

## Customer deletion

- **Assumption:** devices are not transferred between customers; the requirements state a device belongs to a single customer and don't mention resale. Reassigning a device would expose the previous owner's measurements to the new owner. **Future improvement:** store on each measurement the customer that owned the device at measurement time (copied from the device row read during ingestion) and filter reads by it.

**`ON DELETE CASCADE` on `devices.customer_id` and on `measurements.device_id`**

Deleting a customer deletes their devices and all their measurements in a single statement. Both links in the chain need `CASCADE`: with the default (`NO ACTION`) on `measurements.device_id`, the device deletion would fail and the whole customer deletion would be rolled back.

- **GDPR:** hard deletion satisfies the right to erasure with no extra mechanism.
- **Serial numbers:** once the device row is deleted, its `serial_number` is free and can be registered again.
- **Rejected alternative: `ON DELETE RESTRICT`.** Also GDPR-compatible, but the application must delete measurements, devices and customer explicitly, in that order. More code for the same outcome.
- **Rejected alternative: soft delete (`deleted_at`).** Nothing is actually deleted, so it does not satisfy the right to erasure without adding hard deletion or anonymization. The device row also keeps its `serial_number`, so the `UNIQUE` constraint blocks registering the same device again.
- **Known limitation:** deleting a customer with millions of measurements is one long transaction (many row locks, heavy WAL). **Future improvement:** asynchronous deletion: mark the customer as pending deletion, delete measurements in batches, then delete devices and customer.
- **Requires an index on `measurements.device_id`:** Postgres does not create indexes on the referencing side of an FK. Without one, each cascaded device deletion scans the whole `measurements` table.

----

## Indexes

**Main query:** measurements of one device, in a time range, optionally filtered by type, ordered by time.

```sql
SELECT ... FROM measurements
WHERE device_id = $1
  AND "timestamp" >= $2 AND "timestamp" < $3
  [AND type = $4]          -- optional
ORDER BY "timestamp";
```

**Decision: `(device_id, "timestamp", type)` on `measurements`**

```sql
CREATE INDEX measurements_device_id_timestamp_type_idx
    ON measurements (device_id, "timestamp", type);
```

A composite index is a single sorted list, not a chain of separate indexes. A lookup finds where its range starts and walks it contiguously. For device X, entries are ordered like this:

```
(X, 08:00, current)
(X, 08:00, ph)
(X, 08:00, temperature)
(X, 08:01, current)
...
```

- **Equality before range:** `device_id` (equality) goes first, then `timestamp` (range). Once a column is filtered by range, the columns after it can no longer narrow the walked range. So `type` cannot narrow it, but it can still discard non-matching entries inside the index, without reading those rows from the table.
- **Without `type`:** one contiguous range, already ordered by time. No sort needed, and with `LIMIT` the scan stops early (latest 100 measurements → ~100 index entries read).
- **With `type`:** the range contains every type interleaved, so entries of other types are read and discarded. Example: 3 types, one measurement per minute each → a one-day query for `ph` walks 4,320 entries to return 1,440 (3x). The overhead grows with the number of types.
- **Assumption:** devices measure few types, so the overhead with `type` is small and one index serves both cases.
- **Also covers the FK** `measurements.device_id`: Postgres does not index the referencing side of an FK. Since `device_id` is the leading column, this index serves FK checks and cascaded deletes; no separate index is needed.
**When to revisit this decision**

The choice relies on two assumptions: devices measure few types, and queries without `type` (sync/export, device timeline, internal processing) are common enough to favor time order. Interactive use (one chart per metric) mostly filters by `type`, so if devices measured many types and most queries filtered by type, the index would change to `(device_id, type, "timestamp")`, or both indexes would be kept if both query shapes stay frequent. On Postgres 18+, skip scan makes `(device_id, type, "timestamp")` cheaper for queries without `type`, which further favors the change in that scenario.

The decision would be driven by data, not intuition:
- identify the most frequent and most expensive queries with `pg_stat_statements`;
- compare both index orders with `EXPLAIN (ANALYZE, BUFFERS)` on realistic data volumes;
- weigh the read gains against the extra write cost on `measurements`, the most write-heavy table.


**Rejected alternative: `(device_id, type, "timestamp")`.** Optimal with `type` (one contiguous range with exactly the matching entries), but without `type` the day's measurements are split into one block per type:
- before Postgres 18, the index only narrows `device_id`, so the scan walks the device's whole history (e.g. ~1.5M entries for a year) to return one day;
- from Postgres 18, skip scan jumps to the time range inside each type block;
- in both cases rows come out grouped by type, not by time, so they must be sorted, and `LIMIT` cannot stop early.

**Rejected alternative: both indexes.** Optimal for both queries, but every ingested measurement updates two indexes on the most write-heavy table. **Revisit if** devices measure many types or the type distribution is very unequal (a rare type filtered over a busy device reads far more than needed) and type filtering is frequent. The decision would be validated with `EXPLAIN (ANALYZE, BUFFERS)` on realistic data.

**Rejected alternative: `(timestamp, type)` or separate single-column indexes.** Without `device_id` first, a one-device query walks the entries of every device in the time range. Separate single-column indexes cannot serve the combined condition as a single ordered range.

**Other indexes**
- **`devices.customer_id`:** created manually (`CREATE INDEX devices_customer_id_idx ON devices (customer_id)`). It is needed to list a customer's devices and for the cascaded delete from `customers`. Postgres does not create it for the FK.
- **Indexes created automatically:** `PRIMARY KEY` and `UNIQUE` constraints (`devices.serial_number`, `customers.email`, `measurement_types (type, unit)`).
- **Index added by idempotency:** `UNIQUE (device_id, device_measurement_id)` creates a second index on `measurements`. It does not serve the main query (no `timestamp`), and it adds write cost on every insert; it exists only to enforce idempotency.

## Idempotency

### What identifies "the same measurement"

**Decision: a measurement id generated by the device, sent in the payload, unique per device.**

```sql
-- in measurements:
device_measurement_id uuid NOT NULL,
CONSTRAINT measurements_device_id_device_measurement_id_key
    UNIQUE (device_id, device_measurement_id)
```

A measurement is a fact that happened on the device, so its identity is created there. Retries are detected per measurement, regardless of how measurements are grouped into requests.

**Firmware contract:**
- Generate a UUID for each measurement at the moment it is taken. A UUID rather than a counter: a counter that restarts after a factory reset would collide with old ids, and new legitimate measurements would be silently discarded as duplicates.
- Store each pending measurement together with its id, and reuse that same id on every retry, including after reboots. A firmware that generates a new id on retry produces duplicates; that is a firmware contract violation.
- Ids are unique per device across all measurement types. `type` is deliberately not part of the `UNIQUE` constraint: if it were, the same id reused for two types would be stored silently, hiding a firmware bug.

**Rejected alternative: `Idempotency-Key` header with a keys table** (the approach used for payments).
- The key identifies the request, not the measurement. A device with a bad connection can send 50 measurements, time out, reboot, and resend those 50 plus 10 new ones in a new request with a new key: the 50 are stored twice.
- Every request leaves a row in the keys table. It cannot be kept forever (it would grow almost as fast as `measurements`), so keys expire. A device that comes back after days offline retries with expired keys and creates duplicates, and late retries are the normal case in this domain.

**Rejected alternative: natural key `(device_id, type, timestamp)`.**
- Two legitimate, different measurements can share device, type and timestamp, depending on the timestamp precision and on the firmware (e.g. a periodic reading and an event-triggered reading at the same instant). The second one would be discarded as a duplicate and the data lost silently.

**Why no separate keys table is needed** (unlike payments): the operation has no effect outside the database, since the insert is the only effect. And the response can be rebuilt from the stored row. Payments needed to store the full response because a retry could charge the customer twice through an external provider, and the response contained provider data that could not be recomputed.

**Same id, different payload:** the stored measurement is kept, since there is no way to know which value is correct. The incoming measurement is reported as `rejected` in `results` with problem type `measurement-id-conflict`. The request returns `207` if other measurements in the batch succeeded, or `422` if all were rejected; `422` is also the code the IETF `Idempotency-Key` draft recommends when a key is reused with different content. The conflict is logged; it almost always indicates a firmware bug, so it is a good candidate for alerting.

- **Write cost:** the `UNIQUE` constraint adds a second index on `measurements` that every insert must update.

### Concurrent retries

**Problem: check-then-insert.** A handler that first looks for the measurement and inserts it if not found breaks when two retries of the same measurement arrive at the same time: both lookups find nothing and both insert. Without a unique constraint the measurement is duplicated. With `UNIQUE (device_id, device_measurement_id)` there is no duplicate, but the second insert raises a unique violation (`UniqueViolation` in psycopg, `IntegrityError` in SQLAlchemy). Unhandled, it becomes a `500` for a situation that is not a server error, and every race adds noise to error metrics and alerts.

**Decision: a single atomic statement.**

```sql
INSERT INTO measurements (id, device_id, device_measurement_id, type, unit, value, "timestamp")
VALUES (...)
ON CONFLICT (device_id, device_measurement_id) DO NOTHING
RETURNING device_measurement_id;
```

- **A row returned:** the measurement was created.
- **No row returned:** a measurement with that id already exists. The stored row is read and compared with the incoming payload:
  - identical → `duplicate` (a retry), treated as success;
  - different → `rejected` (see *Same id, different payload*).
- **Explicit conflict target:** `ON CONFLICT (device_id, device_measurement_id)` instead of a bare `ON CONFLICT`. A bare `DO NOTHING` swallows violations of any unique constraint on the table, so a future constraint would be silently ignored.

### Status codes

**First ingestion and retry both return `201`.** The firmware only needs to know "stored, remove it from the buffer"; returning the same response for a retry as for the original request avoids making the firmware handle another code, and matches the IETF `Idempotency-Key` draft, which recommends replaying the original response. Whether a measurement was new or a duplicate matters to operators, not to the firmware, so it goes to logs and metrics, not to the status code.

### Batches

**Decision: partial processing with a result per measurement.** Valid measurements are stored even if others in the same batch fail. With all-or-nothing, a single invalid measurement (typically a firmware bug that retries cannot fix) would block every valid measurement in the batch on every retry.

```json
{
  "summary": { "received": 50, "created": 47, "duplicate": 2, "rejected": 1 },
  "results": [
    { "device_measurement_id": "0192a7c4-0001-...", "status": "created" },
    { "device_measurement_id": "0192a7c4-0002-...", "status": "duplicate" },
    {
      "device_measurement_id": "0192a7c4-0003-...",
      "status": "rejected",
      "error": {
        "type": "https://api.kamleon.example/problems/unknown-type-unit",
        "detail": "Unit 'MA' is not registered for type 'current'"
      }
    }
  ]
}
```

- **`results`** has one entry per measurement, in request order, identified by `device_measurement_id` (its identity), not by position.
- **`status`** is a closed set: `created`, `duplicate`, `rejected`.
- **`error.type`** uses the same problem type URIs as whole-request errors (RFC 9457), so per-item and request-level errors share one vocabulary and can be counted and alerted on by type; **`detail`** is for humans.
- **Firmware rule:** `created` and `duplicate` mean "stored, remove from buffer"; `rejected` means "do not retry". A rejected measurement almost always means a firmware bug, so it is logged and alerted on instead of being retried.
- **HTTP status:** all created or duplicate → `201`; all rejected → `422`; mixed → `207 Multi-Status`. Rejected alternative for the mixed case: `200` with per-item results. Both are 2xx, so clients treat them as success either way, but `207` makes partial batches visible in access logs and status-code metrics without inspecting response bodies. `207` comes from WebDAV, where its body is XML; using it with a JSON body is a common convention in REST APIs.
- **Implementation:** the batch is inserted with a single multi-row `INSERT ... ON CONFLICT DO NOTHING RETURNING device_measurement_id`. `RETURNING` yields one row per row actually inserted; conflicting rows yield nothing. The ids missing from the result are the ones that already existed, and only those are read back and compared to classify them as `duplicate` or `rejected`.



## Concurrency

### Ingestion vs. device deactivation

**Assumption: meaning of `inactive`.** A device is inactive when it has been retired/decommissioned or when the customer has withdrawn consent. In both cases nothing more is accepted from that device, regardless of when a measurement was taken. The requirements do not define the meaning of `inactive`, so this is an assumption.

**Rule:** the status is checked when a measurement **arrives**, not against the measurement's `timestamp`.
- **Known limitation:** measurements taken while the device was active but uploaded after deactivation (e.g. the device was offline) are rejected. This is consistent with the assumption: after consent withdrawal, no new data is processed.

**The race (READ COMMITTED):**

```
T1 (ingestion)                    T2 (deactivation)
SELECT status → 'active'
                                  UPDATE devices SET status = 'inactive'
                                  COMMIT
INSERT INTO measurements ...
COMMIT
```

A measurement can be committed a few milliseconds after the deactivation commit, because T1 read `'active'` before the `UPDATE` and does not read it again.

**Decision: no row lock. Declared guarantee: deactivation applies to every measurement that arrives after it is committed; ingestions already in flight at that instant may complete.** The window is milliseconds. GDPR requires stopping processing without undue delay after consent withdrawal; a millisecond window for in-flight requests is not an undue delay. In exchange, ingestion needs no coordination, and there is no lock contention, queueing or starvation.

**Rejected alternative: `SELECT ... FOR UPDATE` on the device row.** It closes the race, but it is an exclusive lock: concurrent ingestions of the same device queue up one after another.

// see IA_LOG.md
**Rejected alternative: `SELECT ... FOR SHARE` on the device row.** It closes the race without queueing ingestions: `FOR SHARE` is compatible with itself, so ingestions of the same device run concurrently, while the deactivation `UPDATE` (which takes `FOR NO KEY UPDATE`) waits for in-flight ingestions to finish. Ingestions arriving after the deactivation commit see `'inactive'` and are rejected. Cost: a lock on every ingestion, and a risk of starvation, since new share-lockers can keep joining while the `UPDATE` waits. Mitigations would be a `lock_timeout` on deactivation with retry, or falling back to `FOR UPDATE`. Not justified for a millisecond window.

**Why the FK alone does not close the race:** inserting a measurement takes `FOR KEY SHARE` on the device row (FK check), which does not conflict with `FOR NO KEY UPDATE`. Postgres allows updating non-key columns of a parent row while children are being inserted, so the status `UPDATE` is not blocked.

**Revisit if** a strict guarantee is required ("no measurement is accepted after the deactivation commit"): use `FOR SHARE` in ingestion plus a `lock_timeout` on deactivation.


### Concurrent device status changes

**Decision: the status endpoint sets a target state ("set status to X"), it does not toggle.**

- **Idempotent:** setting `inactive` on a device that is already `inactive` leaves it `inactive`. The response is `200` with the current state whether or not anything changed: the client asked for the device to be in that state and it is, so there is no error to report. Retries are safe.
- **Concurrent requests:** two requests for the same target state converge on that state in any order. With opposite targets (`active` and `inactive`), the last one to commit wins. Each client receives the real state at the moment its request was applied; a client that checks again later may see a different state set by someone else afterwards, which is expected.

**Rejected alternative: a toggle endpoint ("switch to the opposite state").**
- **Concurrent toggles:** two admins see the device `active` and both press "deactivate", which calls toggle underneath. The two toggles cancel out: the device ends up `active` again while each admin believes they deactivated it.
- **Retries:** a client toggles, gets a timeout and retries. Each retry undoes the previous one, so the final state depends on how many requests actually arrived, which the client cannot know. The operation is not idempotent.

**Rejected alternative: optimistic concurrency control** (the client sends the expected current state with the new one, and the update applies only if the state is still what the client saw). For a single field like `status`, it adds nothing that "set status to X" doesn't already solve. It becomes worthwhile when a client reads several fields, edits some and saves the whole resource: then "apply only if nobody changed it since I read it" prevents overwriting someone else's changes.


### Concurrent registration (device serial number, customer email)

**Problem: check-then-act.** "Check whether the serial (or email) exists, then insert" breaks when two registrations with the same value arrive at the same time: both checks find nothing and both insert. The check is about a row that does not exist yet, and a row that does not exist cannot be locked, so not even `SELECT ... FOR UPDATE` protects it. Only the `UNIQUE` constraint sees both inserts and guarantees a single row.

**Decision: plain `INSERT`, catch `IntegrityError` and map it by constraint name.**

The constraint name is read from the driver error (`e.orig.diag.constraint_name` with psycopg under SQLAlchemy). This is why every constraint is named explicitly in the migrations.

| Violated constraint         | Meaning                   | Response          |
| --------------------------- | ------------------------- | ----------------- |
| `devices_serial_number_key` | serial already registered | `409 Conflict`    |
| `devices_customer_id_fkey`  | customer does not exist   | `404 Not Found`   |
| `customers_email_key`       | email already registered  | `409 Conflict`    |
| any other                   | unexpected, a bug         | re-raised → `500` |

- **`409`:** the request is valid on its own (it would succeed if the value were free) but conflicts with the current state of the server. It is not `400`/`422`: nothing in the request is malformed or invalid.
- **Unknown constraints are re-raised**, never mapped to a generic `409`, so unexpected integrity errors stay visible.
- **After the error the transaction is rolled back.** No follow-up query is needed, since the response does not depend on the existing row.

**Why `409` and not an idempotent success (unlike measurements):** the server cannot tell a retry from a genuine duplicate registration days later, and answering "created" for a resource created three days ago would be misleading. Measurements are sent automatically by firmware, retries are the normal case, and a duplicate must count as success so the device can clear its buffer. Devices and customers are registered occasionally by people or applications; a client that gets `409` after a timeout can look the resource up (by serial or by email) to confirm it exists.

**Rejected alternative: `INSERT ... ON CONFLICT DO NOTHING`.** It avoids the exception for the duplicate case, but an FK violation (missing customer) still raises an error and must be caught anyway, so it would mean two error paths instead of one. Registration is infrequent, so the cost of the exception path (aborted transaction, an `ERROR` line in the Postgres log per duplicate) is negligible. Batch measurement ingestion uses `ON CONFLICT` instead, where the trade-off is the opposite: a single duplicate would abort the whole batch.

**Assumption: the API is internal** (used by Kamleon applications and administrators). A `409` on customer registration reveals that an email is registered (account enumeration). This is addressed by authentication, listed as a future improvement, not by hiding the error. A public sign-up endpoint would instead respond identically in both cases and notify the user by email.


## API contract

### Resource layout: customers

| Method | Route                      | Body                                                   | Responses                                                                                 |
| ------ | -------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `POST` | `/customers`               | `{ "name": "Ana García", "email": "ana@kamleon.com" }` | `201` (+ `Location: /customers/{id}`), `409` email already registered, `422` invalid body |
| `GET`  | `/customers/{customer_id}` | —                                                      | `200`, `404` customer not found                                                           |

- **Email** is lowercased by the application before storing (see *Customer email*).
- **No lookup by email:** the requirements only ask to create and retrieve customers. Unlike devices (a technician holding a device only has the serial on its label), there is no use case for finding a customer by email, and not exposing it avoids revealing which emails are registered.
- **No delete endpoint:** the requirements ask to decide what happens when a customer is deleted, not to expose deletion. The behavior is defined in the schema (`ON DELETE CASCADE`, see *Customer deletion*). **Future improvement:** a `DELETE /customers/{id}` endpoint (with authentication) for GDPR erasure requests.

### Resource layout: devices

**Decision: shallow nesting.** Creating and listing devices is nested under their customer; operating on a single device uses a flat route.

| Method | Route                              | Body                           | Responses                                                                                                          |
| ------ | ---------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `POST` | `/customers/{customer_id}/devices` | `{ "serial_number": "KM123" }` | `201` (+ `Location: /devices/{id}`), `404` customer not found, `409` serial already registered, `422` invalid body |
| `GET`  | `/customers/{customer_id}/devices` | —                              | `200`, `404` customer not found                                                                                    |
| `GET`  | `/devices/{device_id}`             | —                              | `200`, `404`                                                                                                       |
| `GET`  | `/devices?serial_number=KM123`     | —                              | `200` (list, empty if not found)                                                                                   |

- **Nested create/list:** the route expresses ownership, and a missing customer is a natural `404` on the path.
- **Flat single-device routes:** a device is identified by its own id; clients do not need to know its owner to use it. `/customers/{id}/devices/{device_id}` is avoided.
- **Lookup by serial:** serial numbers are globally unique, so a device can be found without knowing its id (e.g. after a `409` on registration).
- **Body contains only client-decided fields:** `customer_id` comes from the path; `id`, `created_at` and `updated_at` are generated by the server.
- **Initial status:** devices are always registered as `active`, so `status` is not part of the creation body. A customer registers a device expecting it to work.
- **One device per request:** each registration gets its own `201`/`409`, with no partial results. Bulk registration is not required.
- **List responses are wrapped:** `{ "items": [...] }` instead of a bare array, so pagination or totals can be added later without breaking clients. A customer has few devices, so the list is not paginated.

**Rejected alternative: `POST /devices` with `customer_id` in the body.** It would suit a future where a device can belong to several customers, but that is not a requirement. With the customer in the body, a missing customer is invalid data in the request rather than a missing resource in the URL, so it would be `422` instead of `404`.

**Future improvement (with authentication):** end-user routes where the customer comes from the token instead of the path (e.g. `GET /me/devices`), so a user cannot read another customer's devices by changing an id in the URL (IDOR). The customer-in-path routes remain for administrators, protected by an admin permission.


### Device status changes

| Method  | Route                  | Body                       | Responses                                                                                                                              |
| ------- | ---------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `PATCH` | `/devices/{device_id}` | `{ "status": "inactive" }` | `200` with the updated device (also when the status was already the requested one), `404` device not found, `422` invalid status value |

**Decision: `PATCH /devices/{device_id}` with the target status in the body.**
- It is a "set status to X" operation, so it is idempotent and safe under concurrent requests and retries (see *Concurrent device status changes*).
- A new status does not change the API: it only becomes a new accepted value. Action routes would need a new URL per state.
- Only `status` is accepted in the body for now; no other device field is modifiable.

**Rejected alternative: action routes** (`POST /devices/{id}/activate`, `POST /devices/{id}/deactivate`). They are also "set status to X" and idempotent, so concurrency does not separate the two options. They also go against the REST convention that URLs name resources and the HTTP method expresses the action. That is a secondary argument: action routes are a widely accepted exception (e.g. Stripe's `POST /payment_intents/{id}/confirm`, Google's API design guide "custom methods" such as `POST /devices/{id}:deactivate`).

**When to revisit:** if transitions gain their own logic, e.g. a required deactivation reason, different permissions per transition, or side effects such as notifications. A single extra field such as a reason could still fit in the `PATCH` body if it is stored on the device. Different payloads, permissions and effects per transition would be better served by action routes, or by modeling the transition as a resource (`POST /devices/{id}/deactivations`, recording reason, time and author).

### Measurement ingestion

| Method | Route                               | Body                                                                                                                                            | Responses                                                                                                                                          |
| ------ | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST` | `/devices/{device_id}/measurements` | `{ "measurements": [ { "device_measurement_id": "…", "type": "ph", "unit": "pH", "value": 7.41, "timestamp": "2026-10-06T09:00:00+02:00" } ] }` | `201` all created or duplicate, `207` mixed result, `422` invalid body or all measurements rejected, `404` device not found, `409` device inactive |

- **Body is always a batch** (`measurements` array, 1 to 1000 items; more → `422`). A single measurement is a batch of one: one endpoint, one response format.
- **Success responses (`201`, `207`)** return the batch result (`summary` + per-measurement `results`), see *Batches*.
- **Device-level errors (`404`, `409`)** reject the whole request with problem details and no per-item results, see *Error mapping*.

### Error mapping

| Case                                                 | Status                                         | Scope                  | Problem `type` (under `/problems/`) |
| ---------------------------------------------------- | ---------------------------------------------- | ---------------------- | ----------------------------------- |
| Customer does not exist when registering a device    | `404 Not Found`                                | whole request          | `customer-not-found`                |
| Serial number already registered                     | `409 Conflict`                                 | whole request          | `serial-already-registered`         |
| Customer email already registered                    | `409 Conflict`                                 | whole request          | `email-already-registered`          |
| Device does not exist when ingesting                 | `404 Not Found`                                | whole request          | `device-not-found`                  |
| Device is inactive when ingesting                    | `409 Conflict`                                 | whole request          | `device-inactive`                   |
| Request body is malformed or fails schema validation | `422 Unprocessable Content`                    | whole request          | `validation-error`                  |
| A single measurement in a batch is invalid           | `207` if others succeeded, `422` if all failed | per item, in `results` | e.g. `unknown-type-unit`            |
| Duplicate measurement (same id, same payload)        | `201 Created`                                  | per item: `duplicate`  | —                                   |
| Same `device_measurement_id`, different payload      | `207` / `422` as above                         | per item: `rejected`   | `measurement-id-conflict`           |

- **Device-level errors apply to the whole request.** All measurements in a batch belong to the same device, so a missing or inactive device rejects the entire request with a single status and no per-item `results`.
- **Two validation levels.** A malformed or schema-invalid body is rejected entirely with `422`. A measurement that is well-formed but cannot be stored is reported per item in `results` (see *Batches*), so the rest of the batch is still processed.
- **Duplicates are a success.** The firmware only needs to know a measurement is stored so it can clear it from its buffer (see *Status codes*).
- **`409` covers several conflicts, so the problem `type` tells them apart** (`serial-already-registered`, `email-already-registered`, `device-inactive`), see *Error format*. Any `4xx` tells the firmware not to retry.
**Inactive device → `409`:** the request is valid but conflicts with the current state of the device, the same reasoning as a duplicate serial.
- **Rejected: `412 Precondition Failed`.** It means a conditional header sent by the client (`If-Match`, `If-Unmodified-Since`) did not hold; the device sends no such condition.
- **Rejected: `403 Forbidden`.** It fits "not allowed" semantically, but it is commonly read as an authentication/authorization problem, and there is no authentication in this API.
- **Rejected: `422`.** It means the request data is invalid; here the data is correct.


### Error format: RFC 9457 (Problem Details)

Every error response uses RFC 9457 with `Content-Type: application/problem+json`.

| Field      | Meaning                                                                             |
| ---------- | ----------------------------------------------------------------------------------- |
| `type`     | URI identifying the problem type; the machine-readable identifier clients branch on |
| `title`    | Short summary of the problem type, the same for every occurrence                    |
| `status`   | HTTP status code, repeated in the body                                              |
| `detail`   | Human-readable explanation of this specific occurrence                              |
| `instance` | URI of this specific occurrence (the request path)                                  |

Problem type URIs live under `https://api.kamleon.example/problems/`. They identify the problem and do not need to resolve to a page.

```json
HTTP/1.1 409 Conflict
Content-Type: application/problem+json

{
  "type": "https://api.kamleon.example/problems/device-inactive",
  "title": "Device is inactive",
  "status": 409,
  "detail": "Device 9f2c4e1a-… is inactive and cannot ingest measurements",
  "instance": "/devices/9f2c4e1a-…/measurements"
}
```

**Validation errors** add an `errors` extension member, one entry per invalid field, located with a JSON Pointer (the pattern used in the RFC's own examples):

```json
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{
  "type": "https://api.kamleon.example/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "errors": [
    { "pointer": "#/measurements/3/timestamp", "detail": "Timestamp must include a UTC offset" }
  ]
}
```

**Rejected alternative: FastAPI's default error format.** It has two shapes (`{"detail": "text"}` for `HTTPException`, `{"detail": [...]}` for validation errors), so clients must check the type of `detail`. It also has no machine-readable identifier, so the conflicts that share `409` (`device-inactive`, `serial-already-registered`) could only be told apart by comparing message text. Fixing either requires custom exception handlers anyway, and at that point a standard format costs the same as an invented one.

**Implementation:** custom FastAPI exception handlers for `RequestValidationError` and for domain errors, converting them to problem details.

### Querying measurements

| Method | Route                               | Query parameters                                                                                                                   | Responses                                               |
| ------ | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `GET`  | `/devices/{device_id}/measurements` | `type` (optional), `from` (optional, default `to - 24h`), `to` (optional, default now), `limit` (optional, default 1000, max 5000) | `200`, `404` device not found, `422` invalid parameters |

```json
{
  "items": [
    { "id": "…", "device_measurement_id": "…", "type": "ph", "value": 7.41, "unit": "pH", "timestamp": "2026-10-06T09:00:00Z" }
  ],
  "has_more": true
}
```

- **Half-open interval `[from, to)`:** consecutive windows (`[00:00, 01:00)`, `[01:00, 02:00)`) never overlap or leave gaps, so no measurement is returned twice or skipped. A closed interval would also force awkward bounds like `to=23:59:59`, missing measurements at `23:59:59.500`.
- **Default range:** `from` defaults to `to - 24h` and `to` defaults to now, so a request without parameters returns the last day. `from` must be earlier than `to`, otherwise `422`. Both must include a UTC offset, same as measurement timestamps.
- **Ordering:** ascending by `timestamp`, served directly by the `(device_id, "timestamp", type)` index without sorting.
- **`limit`:** default `1000`, maximum `5000` (larger values → `422`). A measurement is about 150–200 bytes of JSON, so 5000 rows is about 1 MB per response. A device measuring 3 types once a minute produces about 4,300 measurements per day, so the maximum covers a full day in one request.
- **Truncation is explicit:** if the range contains more rows than `limit`, the first `limit` rows are returned with `"has_more": true`. It is computed by querying `limit + 1` rows, with no extra count query. Without it, a client would assume it received all the data.

**Known limitation:** there is no way to fetch the rows after a truncated response other than narrowing the range.

**Future improvement: cursor (keyset) pagination.**
- **Query:** order by `("timestamp", id)` (`id` breaks ties between equal timestamps) and fetch the rows after the last one seen: `WHERE ("timestamp", id) > ($cursor_ts, $cursor_id) ... LIMIT limit + 1`.
- **Cursor:** the `(timestamp, id)` of the last row, base64-encoded so it is opaque to clients. Returned as `next_cursor` (`null` on the last page) and sent back as `?cursor=`.
- **Rejected alternative: offset pagination.** `OFFSET n` reads and discards `n` rows, so every page gets slower. Rows inserted while paginating shift positions, causing duplicates or gaps. The cursor jumps straight to its position through the index, so every page costs the same, and it is stable under concurrent inserts.
- **For full index support** of the tie-breaker order, `id` would need to be part of the index.


### Health checks

| Method | Route           | Checks                                   | Responses                                                                        |
| ------ | --------------- | ---------------------------------------- | -------------------------------------------------------------------------------- |
| `GET`  | `/health/live`  | the process responds; no database access | `200 { "status": "ok" }`                                                         |
| `GET`  | `/health/ready` | the database answers `SELECT 1`          | `200 { "status": "ok" }`, `503` (problem details) if the database is unreachable |

**Decision: two endpoints, liveness and readiness.** The requirement asks for a basic health check; both together cost a few lines and keep two different questions separate.
- **Liveness (`/health/live`)** answers "is the process alive?". An orchestrator uses it to decide whether to **restart** the container. It must not touch the database: during a database outage every instance would fail it and be restarted in a loop, and restarting the API does not fix the database.
- **Readiness (`/health/ready`)** answers "can this instance serve requests?". A load balancer uses it to **stop sending traffic** to an instance that cannot reach the database, without restarting it.
- In `docker-compose`, `/health/ready` can serve as the API container's healthcheck, so the stack only reports healthy when the API can actually reach the database.


## Stack

- **SQLAlchemy 2.x (ORM, 2.0 style) + Alembic:** the de facto standard for database access and migrations in Python outside Django, and the usual pairing with FastAPI, so any reviewer knows it. Everything decided here is expressible with it (`ON CONFLICT` via the PostgreSQL dialect's `insert(...).on_conflict_do_nothing()`, `FOR SHARE` via `with_for_update(read=True)`, named `CHECK`/`UNIQUE`/FK constraints). The ORM, with typed models (`Mapped[...]`), keeps row-to-object mapping out of the codebase and is the most common way SQLAlchemy is used with FastAPI; PostgreSQL-specific statements are still written with SQLAlchemy's statement API where needed. Rejected: SQLAlchemy Core only (closer to SQL, but row mapping would be written by hand) and hand-written SQL with a raw driver (more control and transparency, but row mapping costs time and it is a non-standard choice to justify).
- **Driver: psycopg 3:** the successor of psycopg2, the most widely used Postgres driver in Python. It supports both sync and async, and it exposes the violated constraint name (`diag.constraint_name`) used to map `IntegrityError` to HTTP responses. Rejected: asyncpg (async-only, very fast, but its own API and error model).
- **Async:** requests spend almost all their time waiting on the database, not computing. A sync FastAPI app also serves concurrent requests, but each one holds a thread from a limited pool while it waits; with async, a waiting request yields the event loop and does not hold a thread. Cost: every I/O call must be async, since a single blocking call inside an async endpoint stalls every request on that event loop.
- **Tests: `pytest` against a real Postgres, not SQLite:**
  - SQLite has no row-level locks (no `FOR SHARE`/`FOR UPDATE`; a write locks the whole database), so concurrency tests (simultaneous retries, duplicate registrations) would not test Postgres behavior.
  - SQLite has foreign keys disabled by default (`PRAGMA foreign_keys = ON`), so cascaded deletes and the `(type, unit)` composite FK would silently not be enforced.
  - It also has no `timestamptz` (dates stored as text/numbers, no time zone conversion) and different error details, so time-range queries and constraint-name error mapping would not be tested either.

## Architecture

**Decision: layered architecture (router → service → repository) with a light domain layer.** Business rules are pure functions in `domain/` that receive plain values and have no FastAPI or SQLAlchemy imports, so they are testable without a database. Services orchestrate use cases with SQLAlchemy ORM models directly.

- **Rejected alternative: full hexagonal architecture** (domain entities as dataclasses, repositories mapping rows to them, services never seeing ORM models). It removes all coupling to the ORM, but with three entities the mapping layer is more code than value for this scope.
- **Trade-off accepted:** services depend on ORM models, so renaming a model attribute touches the services that use it.
- **Evolution path:** if the domain grows (more rules, more entities, or a need to change persistence), introduce domain entities and make repositories map to them. The layering already allows it without touching routers.