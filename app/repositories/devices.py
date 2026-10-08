import uuid
from collections.abc import Sequence

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeviceStatus
from app.domain.errors import CustomerNotFoundError, SerialAlreadyRegisteredError
from app.models import Device


async def create(session: AsyncSession, *, customer_id: uuid.UUID, serial_number: str) -> Device:
    """Plain INSERT: the constraints decide, no lookup first."""
    try:
        result = await session.execute(
            insert(Device)
            .values(
                customer_id=customer_id, serial_number=serial_number, status=DeviceStatus.ACTIVE
            )
            .returning(Device)
        )
    except IntegrityError as e:
        constraint = getattr(e.orig.diag, "constraint_name", None)  # type: ignore[union-attr]
        if constraint == "devices_serial_number_key":
            raise SerialAlreadyRegisteredError(serial_number) from e
        if constraint == "devices_customer_id_fkey":
            raise CustomerNotFoundError(customer_id) from e
        raise
    return result.scalar_one()


async def get(session: AsyncSession, device_id: uuid.UUID) -> Device | None:
    return await session.get(Device, device_id)


async def list_by_customer(session: AsyncSession, customer_id: uuid.UUID) -> Sequence[Device]:
    result = await session.scalars(
        select(Device)
        .where(Device.customer_id == customer_id)
        .order_by(Device.created_at, Device.id)
    )
    return result.all()


async def list_by_serial_number(session: AsyncSession, serial_number: str) -> Sequence[Device]:
    """Serials are unique, so the list has at most one device."""
    result = await session.scalars(select(Device).where(Device.serial_number == serial_number))
    return result.all()


async def set_status(
    session: AsyncSession, device_id: uuid.UUID, status: DeviceStatus
) -> Device | None:
    """Return the device if its status changed, None if it was already `status` or is missing.

    IS DISTINCT FROM keeps updated_at untouched on a no-op (DECISIONS.md, Device status changes).
    """
    result = await session.execute(
        update(Device)
        .where(Device.id == device_id, Device.status.is_distinct_from(status))
        .values(status=status)
        .returning(Device)
    )
    return result.scalar_one_or_none()
