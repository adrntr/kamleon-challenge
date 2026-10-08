"""Initial schema: customers, devices, measurement types and measurements.

Constraint and index names are written out in full (op.f) so they never depend
on the naming convention at the time the migration runs.

Revision ID: 0001
Revises:
Create Date: 2026-10-08 13:24:03.025920
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("customers_pkey")),
        sa.UniqueConstraint("email", name=op.f("customers_email_key")),
        sa.CheckConstraint("email = lower(email)", name=op.f("customers_email_lowercase_check")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("customers_name_not_blank_check")),
        sa.CheckConstraint("btrim(email) <> ''", name=op.f("customers_email_not_blank_check")),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("devices_pkey")),
        sa.UniqueConstraint("serial_number", name=op.f("devices_serial_number_key")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("devices_customer_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name=op.f("devices_status_check")),
        sa.CheckConstraint(
            "btrim(serial_number) <> ''", name=op.f("devices_serial_number_not_blank_check")
        ),
    )
    op.create_index(op.f("devices_customer_id_idx"), "devices", ["customer_id"])

    op.create_table(
        "measurement_types",
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("type", "unit", name=op.f("measurement_types_pkey")),
        sa.CheckConstraint(
            "btrim(type) <> ''", name=op.f("measurement_types_type_not_blank_check")
        ),
        sa.CheckConstraint(
            "btrim(unit) <> ''", name=op.f("measurement_types_unit_not_blank_check")
        ),
    )

    op.create_table(
        "measurements",
        # No server default: ids are generated in the application with uuid.uuid7().
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("device_measurement_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("measurements_pkey")),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("measurements_device_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type", "unit"],
            ["measurement_types.type", "measurement_types.unit"],
            name=op.f("measurements_type_unit_fkey"),
        ),
        sa.UniqueConstraint(
            "device_id",
            "device_measurement_id",
            name=op.f("measurements_device_id_device_measurement_id_key"),
        ),
    )
    op.create_index(
        op.f("measurements_device_id_timestamp_type_idx"),
        "measurements",
        ["device_id", "timestamp", "type"],
    )


def downgrade() -> None:
    op.drop_table("measurements")
    op.drop_table("measurement_types")
    op.drop_table("devices")
    op.drop_table("customers")
