"""The migrated schema: names, seed data and the invariants enforced by the database."""

import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.enums import DeviceStatus
from app.models import Base, Device

EXPECTED_CONSTRAINTS = {
    ("customers", "customers_pkey"),
    ("customers", "customers_email_key"),
    ("customers", "customers_email_lowercase_check"),
    ("customers", "customers_name_not_blank_check"),
    ("customers", "customers_email_not_blank_check"),
    ("devices", "devices_pkey"),
    ("devices", "devices_serial_number_key"),
    ("devices", "devices_customer_id_fkey"),
    ("devices", "devices_status_check"),
    ("devices", "devices_serial_number_not_blank_check"),
    ("measurement_types", "measurement_types_pkey"),
    ("measurement_types", "measurement_types_type_not_blank_check"),
    ("measurement_types", "measurement_types_unit_not_blank_check"),
    ("measurements", "measurements_pkey"),
    ("measurements", "measurements_device_id_fkey"),
    ("measurements", "measurements_type_unit_fkey"),
    ("measurements", "measurements_device_id_device_measurement_id_key"),
}

# Indexes that do not back a constraint.
EXPECTED_INDEXES = {
    ("devices", "devices_customer_id_idx"),
    ("measurements", "measurements_device_id_timestamp_type_idx"),
}

EXPECTED_MEASUREMENT_TYPES = {
    ("ph", "pH"),
    ("current", "mA"),
    ("temperature", "degC"),
    ("conductivity", "mS/cm"),
    ("volume", "mL"),
    ("flow_rate", "mL/s"),
}

CUSTOMER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEVICE_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")

INSERT_CUSTOMER = (
    "INSERT INTO customers (id, name, email) VALUES (:customer_id, 'Ana', 'ana@x.com')"
)
INSERT_DEVICE = (
    "INSERT INTO devices (id, customer_id, serial_number, status) "
    "VALUES (:device_id, :customer_id, 'KM1', 'active')"
)
INSERT_MEASUREMENT = (
    "INSERT INTO measurements (id, device_id, device_measurement_id, type, unit, value, timestamp) "
    "VALUES (gen_random_uuid(), :device_id, gen_random_uuid(), 'ph', 'pH', 7.4, now())"
)


def test_constraint_names_match_decisions(db: Engine) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conrelid::regclass::text, conname FROM pg_constraint "
                "WHERE connamespace = 'public'::regnamespace "
                "AND contype <> 'n' "  # NOT NULL constraints, cataloged since Postgres 18
                "AND conrelid::regclass::text <> 'alembic_version'"
            )
        )
        assert {tuple(row) for row in rows} == EXPECTED_CONSTRAINTS


def test_index_names_match_decisions(db: Engine) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version' "
                "AND indexname NOT IN (SELECT conname FROM pg_constraint)"
            )
        )
        assert {tuple(row) for row in rows} == EXPECTED_INDEXES


def test_models_match_migrations(db: Engine) -> None:
    with db.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_measurement_types_are_seeded(db: Engine) -> None:
    with db.connect() as connection:
        rows = connection.execute(text("SELECT type, unit FROM measurement_types"))
        assert {tuple(row) for row in rows} == EXPECTED_MEASUREMENT_TYPES


def test_session_time_zone_is_utc(db: Engine) -> None:
    with db.connect() as connection:
        assert connection.scalar(text("SHOW timezone")) == "UTC"


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO customers (id, name, email) VALUES (gen_random_uuid(), 'Ana', 'A@x.com')",
            "customers_email_lowercase_check",
        ),
        (
            "INSERT INTO customers (id, name, email) VALUES (gen_random_uuid(), '  ', 'b@x.com')",
            "customers_name_not_blank_check",
        ),
        (
            "INSERT INTO customers (id, name, email) VALUES (gen_random_uuid(), 'Ana', ' ')",
            "customers_email_not_blank_check",
        ),
        (
            "INSERT INTO customers (id, name, email) VALUES (gen_random_uuid(), 'E', 'ana@x.com')",
            "customers_email_key",
        ),
        (
            "INSERT INTO devices (id, customer_id, serial_number, status) "
            "VALUES (gen_random_uuid(), :customer_id, 'KM2', 'Active')",
            "devices_status_check",
        ),
        (
            "INSERT INTO devices (id, customer_id, serial_number, status) "
            "VALUES (gen_random_uuid(), :customer_id, ' ', 'active')",
            "devices_serial_number_not_blank_check",
        ),
        (
            "INSERT INTO devices (id, customer_id, serial_number, status) "
            "VALUES (gen_random_uuid(), :customer_id, 'KM1', 'active')",
            "devices_serial_number_key",
        ),
        (
            "INSERT INTO devices (id, customer_id, serial_number, status) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), 'KM2', 'active')",
            "devices_customer_id_fkey",
        ),
        (
            "INSERT INTO measurement_types VALUES ('', 'mA')",
            "measurement_types_type_not_blank_check",
        ),
        (
            "INSERT INTO measurement_types VALUES ('ph', ' ')",
            "measurement_types_unit_not_blank_check",
        ),
        (
            "INSERT INTO measurements (id, device_id, device_measurement_id, type, unit, value, "
            "timestamp) VALUES (gen_random_uuid(), :device_id, gen_random_uuid(), 'ph', 'mA', 1, "
            "now())",
            "measurements_type_unit_fkey",
        ),
        (
            "INSERT INTO measurements (id, device_id, device_measurement_id, type, unit, value, "
            "timestamp) VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'ph', "
            "'pH', 1, now())",
            "measurements_device_id_fkey",
        ),
    ],
)
def test_database_rejects_invalid_rows(db: Engine, statement: str, constraint: str) -> None:
    params = {"customer_id": CUSTOMER_ID, "device_id": DEVICE_ID}
    with db.begin() as connection:
        connection.execute(text(INSERT_CUSTOMER), params)
        connection.execute(text(INSERT_DEVICE), params)

    with pytest.raises(DBAPIError) as excinfo, db.begin() as connection:
        connection.execute(text(statement), params)

    assert excinfo.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "statement",
    [
        # Ids and the initial device status have no server default.
        "INSERT INTO customers (name, email) VALUES ('Eva', 'eva@x.com')",
        "INSERT INTO devices (id, customer_id, serial_number) "
        "VALUES (gen_random_uuid(), :customer_id, 'KM2')",
        "INSERT INTO measurements (device_id, device_measurement_id, type, unit, value, timestamp) "
        "VALUES (:device_id, gen_random_uuid(), 'ph', 'pH', 1, now())",
    ],
)
def test_omitted_columns_without_default_are_rejected(db: Engine, statement: str) -> None:
    params = {"customer_id": CUSTOMER_ID, "device_id": DEVICE_ID}
    with db.begin() as connection:
        connection.execute(text(INSERT_CUSTOMER), params)
        connection.execute(text(INSERT_DEVICE), params)

    with pytest.raises(DBAPIError) as excinfo, db.begin() as connection:
        connection.execute(text(statement), params)

    assert excinfo.value.orig.sqlstate == "23502"  # not_null_violation


def test_deleting_a_customer_cascades_to_devices_and_measurements(db: Engine) -> None:
    params = {"customer_id": CUSTOMER_ID, "device_id": DEVICE_ID}
    with db.begin() as connection:
        connection.execute(text(INSERT_CUSTOMER), params)
        connection.execute(text(INSERT_DEVICE), params)
        connection.execute(text(INSERT_MEASUREMENT), params)

    with db.begin() as connection:
        connection.execute(text("DELETE FROM customers WHERE id = :customer_id"), params)

    with db.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM devices")) == 0
        assert connection.scalar(text("SELECT count(*) FROM measurements")) == 0


def test_measurement_type_in_use_cannot_be_removed(db: Engine) -> None:
    params = {"customer_id": CUSTOMER_ID, "device_id": DEVICE_ID}
    with db.begin() as connection:
        connection.execute(text(INSERT_CUSTOMER), params)
        connection.execute(text(INSERT_DEVICE), params)
        connection.execute(text(INSERT_MEASUREMENT), params)

    with pytest.raises(DBAPIError) as excinfo, db.begin() as connection:
        connection.execute(text("DELETE FROM measurement_types WHERE type = 'ph'"))

    assert excinfo.value.orig.diag.constraint_name == "measurements_type_unit_fkey"


def test_device_updated_at_changes_on_orm_update(db: Engine) -> None:
    def updated_at() -> object:
        with db.connect() as connection:
            return connection.scalar(
                text("SELECT updated_at FROM devices WHERE id = :id"), {"id": DEVICE_ID}
            )

    with Session(db) as session, session.begin():
        session.execute(text(INSERT_CUSTOMER), {"customer_id": CUSTOMER_ID})
        session.add(
            Device(
                id=DEVICE_ID,
                customer_id=CUSTOMER_ID,
                serial_number="KM1",
                status=DeviceStatus.ACTIVE,
            )
        )
    created = updated_at()

    with Session(db) as session, session.begin():
        session.get_one(Device, DEVICE_ID).status = DeviceStatus.INACTIVE

    assert updated_at() > created
