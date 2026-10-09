import uuid
from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import DeviceNotFoundError
from app.domain.measurements import MeasurementPayload
from app.models import Measurement, MeasurementType


async def registered_pairs(
    session: AsyncSession, pairs: Collection[tuple[str, str]]
) -> set[tuple[str, str]]:
    """The subset of `pairs` registered in measurement_types.

    Only for per-item error messages: measurements_type_unit_fkey stays the guarantee.
    """
    result = await session.execute(
        select(MeasurementType.type, MeasurementType.unit).where(
            tuple_(MeasurementType.type, MeasurementType.unit).in_(list(pairs))
        )
    )
    return {(row.type, row.unit) for row in result}


async def insert_if_absent(
    session: AsyncSession,
    device_id: uuid.UUID,
    measurements: Collection[MeasurementPayload],
) -> set[uuid.UUID]:
    """Insert every measurement whose (device_id, device_measurement_id) is absent, in one
    statement. Returns the device_measurement_ids actually inserted.

    RETURNING yields only inserted rows, in no guaranteed order, so callers match by id.
    The conflict target is explicit so that a violation of any other unique constraint
    still raises (DECISIONS.md, Concurrent retries). If another transaction holds an
    uncommitted row with the same key, this waits for it. Rows are sent sorted by id so
    that concurrent batches sharing ids take their locks in the same order.
    """
    rows = [
        {
            "id": uuid.uuid7(),
            "device_id": device_id,
            "device_measurement_id": m.device_measurement_id,
            "type": m.type,
            "unit": m.unit,
            "value": m.value,
            "timestamp": m.timestamp,
        }
        for m in sorted(measurements, key=lambda m: m.device_measurement_id)
    ]
    statement = (
        insert(Measurement)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["device_id", "device_measurement_id"])
        .returning(Measurement.device_measurement_id)
    )
    try:
        result = await session.execute(statement)
    except IntegrityError as e:
        # The device was deleted after its status was read.
        if getattr(e.orig.diag, "constraint_name", None) == "measurements_device_id_fkey":  # type: ignore[union-attr]
            raise DeviceNotFoundError(device_id) from e
        raise
    return set(result.scalars())


async def get_by_device_measurement_ids(
    session: AsyncSession, device_id: uuid.UUID, device_measurement_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Measurement]:
    """The stored measurements among `device_measurement_ids`, keyed by that id."""
    result = await session.scalars(
        select(Measurement).where(
            Measurement.device_id == device_id,
            Measurement.device_measurement_id.in_(list(device_measurement_ids)),
        )
    )
    return {m.device_measurement_id: m for m in result}


async def list_in_range(
    session: AsyncSession,
    device_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
    type_: str | None,
    limit: int,
) -> Sequence[Measurement]:
    """Measurements in [start, end), oldest first, read in order from
    measurements_device_id_timestamp_type_idx (DECISIONS.md, Indexes)."""
    statement = select(Measurement).where(
        Measurement.device_id == device_id,
        Measurement.timestamp >= start,
        Measurement.timestamp < end,
    )
    if type_ is not None:
        statement = statement.where(Measurement.type == type_)
    result = await session.scalars(statement.order_by(Measurement.timestamp).limit(limit))
    return result.all()
