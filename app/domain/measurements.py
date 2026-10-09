"""Measurement ingestion: per-item outcomes and the rule that tells a retry from a conflict."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class MeasurementPayload:
    """A measurement as sent by the device, before it is stored."""

    device_measurement_id: uuid.UUID
    type: str
    unit: str
    value: float
    timestamp: datetime


class ItemStatus(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class RejectionReason(StrEnum):
    """Values are the problem type identifiers used in the API, so logs and responses
    can be searched and alerted on with the same string."""

    UNKNOWN_TYPE_UNIT = "unknown-type-unit"
    MEASUREMENT_ID_CONFLICT = "measurement-id-conflict"


@dataclass(frozen=True)
class Rejection:
    reason: RejectionReason
    detail: str


@dataclass(frozen=True)
class ItemResult:
    device_measurement_id: uuid.UUID
    status: ItemStatus
    rejection: Rejection | None = None


@dataclass(frozen=True)
class BatchResult:
    """One result per measurement, in request order."""

    items: tuple[ItemResult, ...]

    def count(self, status: ItemStatus) -> int:
        return sum(1 for item in self.items if item.status is status)

    @property
    def all_rejected(self) -> bool:
        return self.count(ItemStatus.REJECTED) == len(self.items)


def unknown_type_unit(measurement: MeasurementPayload) -> Rejection:
    return Rejection(
        RejectionReason.UNKNOWN_TYPE_UNIT,
        f"Unit '{measurement.unit}' is not registered for type '{measurement.type}'",
    )


def measurement_id_conflict(measurement: MeasurementPayload) -> Rejection:
    # Never the values: they are health data.
    return Rejection(
        RejectionReason.MEASUREMENT_ID_CONFLICT,
        f"Measurement {measurement.device_measurement_id} is already stored "
        "with a different payload",
    )


def is_same_measurement(stored: MeasurementPayload, incoming: MeasurementPayload) -> bool:
    """A retry carries exactly the stored payload (DECISIONS.md, Same id, different payload).

    Timestamps are aware datetimes, so == compares instants: 09:00+02:00 equals 07:00Z.
    Values are compared exactly, with no tolerance: double precision round-trips the
    float the device sent, so a genuine retry is always bit-identical.
    """
    return (
        stored.type == incoming.type
        and stored.unit == incoming.unit
        and stored.value == incoming.value
        and stored.timestamp == incoming.timestamp
    )
