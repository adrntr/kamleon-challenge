import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.dependencies import SessionDep
from app.api.problems import PROBLEM_RESPONSE
from app.schemas.device import DeviceCreate, DeviceList, DeviceRead, DeviceStatusUpdate
from app.services import devices as device_service

customer_devices_router = APIRouter(prefix="/customers/{customer_id}/devices", tags=["devices"])
router = APIRouter(prefix="/devices", tags=["devices"])


@customer_devices_router.post(
    "",
    status_code=201,
    responses={
        404: {**PROBLEM_RESPONSE, "description": "Customer not found (customer-not-found)"},
        409: {
            **PROBLEM_RESPONSE,
            "description": "Serial already registered (serial-already-registered)",
        },
        422: {**PROBLEM_RESPONSE, "description": "Invalid body or id (validation-error)"},
    },
)
async def create_device(
    customer_id: uuid.UUID, body: DeviceCreate, session: SessionDep, response: Response
) -> DeviceRead:
    """Register a device for a customer. It starts as active."""
    device = await device_service.register_device(
        session, customer_id=customer_id, serial_number=body.serial_number
    )
    response.headers["Location"] = f"/devices/{device.id}"
    return DeviceRead.model_validate(device)


@customer_devices_router.get(
    "",
    responses={
        404: {**PROBLEM_RESPONSE, "description": "Customer not found (customer-not-found)"},
        422: {**PROBLEM_RESPONSE, "description": "Malformed customer id (validation-error)"},
    },
)
async def list_customer_devices(customer_id: uuid.UUID, session: SessionDep) -> DeviceList:
    """List the devices of a customer."""
    devices = await device_service.list_customer_devices(session, customer_id)
    return DeviceList(items=[DeviceRead.model_validate(device) for device in devices])


@router.get(
    "/{device_id}",
    responses={
        404: {**PROBLEM_RESPONSE, "description": "Device not found (device-not-found)"},
        422: {**PROBLEM_RESPONSE, "description": "Malformed device id (validation-error)"},
    },
)
async def get_device(device_id: uuid.UUID, session: SessionDep) -> DeviceRead:
    """Retrieve a device by id."""
    device = await device_service.get_device(session, device_id)
    return DeviceRead.model_validate(device)


@router.patch(
    "/{device_id}",
    responses={
        404: {**PROBLEM_RESPONSE, "description": "Device not found (device-not-found)"},
        422: {**PROBLEM_RESPONSE, "description": "Invalid id or status (validation-error)"},
    },
)
async def update_device_status(
    device_id: uuid.UUID, body: DeviceStatusUpdate, session: SessionDep
) -> DeviceRead:
    """Set the status of a device. Idempotent: the current device is returned either way."""
    device = await device_service.set_device_status(session, device_id, body.status)
    return DeviceRead.model_validate(device)


@router.get(
    "",
    responses={
        422: {
            **PROBLEM_RESPONSE,
            "description": "Missing or empty serial_number (validation-error)",
        },
    },
)
async def find_devices(
    serial_number: Annotated[
        str,
        Query(min_length=1, description="Exact serial number to look up.", examples=["KM123"]),
    ],
    session: SessionDep,
) -> DeviceList:
    """Find a device by serial number. The list is empty if none matches."""
    devices = await device_service.find_devices_by_serial(session, serial_number.strip())
    return DeviceList(items=[DeviceRead.model_validate(device) for device in devices])
