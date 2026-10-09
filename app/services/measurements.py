import logging
import uuid
from collections import Counter
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeviceStatus
from app.domain.errors import BatchRejectedError, DeviceInactiveError, DeviceNotFoundError
from app.domain.measurements import (
    BatchResult,
    ItemResult,
    ItemStatus,
    MeasurementPayload,
    Rejection,
    is_same_measurement,
    measurement_id_conflict,
    unknown_type_unit,
)
from app.models import Measurement
from app.repositories import devices as device_repository
from app.repositories import measurements as measurement_repository

logger = logging.getLogger(__name__)


async def ingest_measurements(
    session: AsyncSession, device_id: uuid.UUID, measurements: Sequence[MeasurementPayload]
) -> BatchResult:
    """Store a batch with one result per measurement (DECISIONS.md, Batches).

    Raises BatchRejectedError if every measurement is rejected.
    """
    async with session.begin():
        # No row lock: a deactivation committed meanwhile does not stop this
        # in-flight request (DECISIONS.md, Ingestion vs. device deactivation).
        device = await device_repository.get(session, device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        if device.status != DeviceStatus.ACTIVE:
            raise DeviceInactiveError(device_id)

        registered = await measurement_repository.registered_pairs(
            session, {(m.type, m.unit) for m in measurements}
        )
        insertable = [m for m in measurements if (m.type, m.unit) in registered]
        inserted = await _insert(session, device_id, insertable)
        stored = await _read_back(session, device_id, insertable, inserted)
    result = BatchResult(tuple(_classify(m, registered, inserted, stored) for m in measurements))

    _log_outcomes(device_id, measurements, result)
    if result.all_rejected:
        raise BatchRejectedError(result)
    return result


async def _insert(
    session: AsyncSession, device_id: uuid.UUID, measurements: Sequence[MeasurementPayload]
) -> set[uuid.UUID]:
    """One INSERT for the batch; the unique constraint decides which rows are new."""
    if not measurements:
        return set()
    return await measurement_repository.insert_if_absent(session, device_id, measurements)


async def _read_back(
    session: AsyncSession,
    device_id: uuid.UUID,
    measurements: Sequence[MeasurementPayload],
    inserted: set[uuid.UUID],
) -> dict[uuid.UUID, Measurement]:
    """One SELECT for every id the INSERT skipped, to tell duplicates from conflicts."""
    existing = {m.device_measurement_id for m in measurements} - inserted
    if not existing:
        return {}
    stored = await measurement_repository.get_by_device_measurement_ids(
        session, device_id, existing
    )
    if stored.keys() != existing:
        # Deleted between the INSERT and this SELECT (DECISIONS.md, Ingestion vs. deletion).
        if not await device_repository.exists(session, device_id):
            raise DeviceNotFoundError(device_id)
        # Removed outside the model; never re-inserted, the deletion may be deliberate.
        raise RuntimeError(
            f"device {device_id}: {len(existing - stored.keys())} measurement(s) skipped by "
            "the INSERT as existing are missing from the read-back"
        )
    return stored


def _classify(
    measurement: MeasurementPayload,
    registered: set[tuple[str, str]],
    inserted: set[uuid.UUID],
    stored: dict[uuid.UUID, Measurement],
) -> ItemResult:
    if (measurement.type, measurement.unit) not in registered:
        return _rejected(measurement, unknown_type_unit(measurement))
    if measurement.device_measurement_id in inserted:
        return ItemResult(measurement.device_measurement_id, ItemStatus.CREATED)
    if is_same_measurement(_payload(stored[measurement.device_measurement_id]), measurement):
        return ItemResult(measurement.device_measurement_id, ItemStatus.DUPLICATE)
    return _rejected(measurement, measurement_id_conflict(measurement))


def _payload(stored: Measurement) -> MeasurementPayload:
    return MeasurementPayload(
        device_measurement_id=stored.device_measurement_id,
        type=stored.type,
        unit=stored.unit,
        value=stored.value,
        timestamp=stored.timestamp,
    )


def _rejected(measurement: MeasurementPayload, rejection: Rejection) -> ItemResult:
    return ItemResult(measurement.device_measurement_id, ItemStatus.REJECTED, rejection)


def _log_outcomes(
    device_id: uuid.UUID, measurements: Sequence[MeasurementPayload], result: BatchResult
) -> None:
    """One line per (outcome, type, unit) with its count, never the values.

    Aggregated so that a misconfigured firmware sending 1000 items logs one line, not 1000.
    """
    counts = Counter(
        (item.rejection.reason if item.rejection else item.status, m.type, m.unit)
        for m, item in zip(measurements, result.items, strict=True)
        if item.status is not ItemStatus.CREATED
    )
    for (outcome, type_, unit), count in counts.items():
        level = logging.INFO if outcome is ItemStatus.DUPLICATE else logging.WARNING
        logger.log(
            level,
            "device %s: %d measurement(s) %s (type=%s, unit=%s)",
            device_id,
            count,
            outcome.value,
            type_,
            unit,
        )
