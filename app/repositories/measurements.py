import uuid
from collections.abc import Collection

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
    *,
    measurement_id: uuid.UUID,
    device_id: uuid.UUID,
    measurement: MeasurementPayload,
) -> bool:
    """Insert unless (device_id, device_measurement_id) exists. True if a row was inserted.

    The conflict target is explicit so that a violation of any other unique
    constraint still raises (DECISIONS.md, Concurrent retries). If another
    transaction holds an uncommitted row with the same key, this waits for it.
    """
    statement = (
        insert(Measurement)
        .values(
            id=measurement_id,
            device_id=device_id,
            device_measurement_id=measurement.device_measurement_id,
            type=measurement.type,
            unit=measurement.unit,
            value=measurement.value,
            timestamp=measurement.timestamp,
        )
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
    return result.scalar_one_or_none() is not None


async def get_by_device_measurement_id(
    session: AsyncSession, device_id: uuid.UUID, device_measurement_id: uuid.UUID
) -> Measurement | None:
    return await session.scalar(
        select(Measurement).where(
            Measurement.device_id == device_id,
            Measurement.device_measurement_id == device_measurement_id,
        )
    )
