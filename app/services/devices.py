import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeviceStatus
from app.domain.errors import CustomerNotFoundError, DeviceNotFoundError
from app.models import Device
from app.repositories import customers as customer_repository
from app.repositories import devices as device_repository


async def register_device(
    session: AsyncSession, *, customer_id: uuid.UUID, serial_number: str
) -> Device:
    """Devices are always registered as active (docs/DECISIONS.md, Resource layout: devices)."""
    async with session.begin():
        return await device_repository.create(
            session, customer_id=customer_id, serial_number=serial_number
        )


async def get_device(session: AsyncSession, device_id: uuid.UUID) -> Device:
    async with session.begin():
        device = await device_repository.get(session, device_id)
    if device is None:
        raise DeviceNotFoundError(device_id)
    return device


async def set_device_status(
    session: AsyncSession, device_id: uuid.UUID, status: DeviceStatus
) -> Device:
    """Set the target status. Idempotent: an already-current status returns the device untouched."""
    async with session.begin():
        device = await device_repository.set_status(session, device_id, status)
        if device is None:
            device = await device_repository.get(session, device_id)
    if device is None:
        raise DeviceNotFoundError(device_id)
    return device


async def list_customer_devices(session: AsyncSession, customer_id: uuid.UUID) -> Sequence[Device]:
    async with session.begin():
        if await customer_repository.get(session, customer_id) is None:
            raise CustomerNotFoundError(customer_id)
        return await device_repository.list_by_customer(session, customer_id)


async def find_devices_by_serial(session: AsyncSession, serial_number: str) -> Sequence[Device]:
    async with session.begin():
        return await device_repository.list_by_serial_number(session, serial_number)
