"""Seed the initial measurement types.

The list is an assumption: the requirements do not define it (DECISIONS.md,
Measurement types and units). Units are ASCII codes. A new type is added with
a new migration like this one.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Defined here instead of importing the ORM model, so this migration keeps
# working when the model changes.
measurement_types = sa.table(
    "measurement_types",
    sa.column("type", sa.Text()),
    sa.column("unit", sa.Text()),
)

SEED = [
    ("ph", "pH"),
    ("current", "mA"),
    ("temperature", "degC"),
    ("conductivity", "mS/cm"),
    ("volume", "mL"),
    ("flow_rate", "mL/s"),
]


def upgrade() -> None:
    op.bulk_insert(measurement_types, [{"type": t, "unit": u} for t, u in SEED])


def downgrade() -> None:
    # Fails with measurements_type_unit_fkey if a measurement still uses a pair.
    op.execute(
        measurement_types.delete().where(
            sa.tuple_(measurement_types.c.type, measurement_types.c.unit).in_(SEED)
        )
    )
