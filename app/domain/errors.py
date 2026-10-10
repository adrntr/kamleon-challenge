from app.domain.measurements import BatchResult, ItemStatus
from app.errors import AppError


class DomainError(AppError):
    """Base class for errors raised by business rules."""


class CustomerNotFoundError(DomainError):
    def __init__(self, customer_id: object) -> None:
        super().__init__(f"Customer {customer_id} does not exist")


class EmailAlreadyRegisteredError(DomainError):
    def __init__(self, email: str) -> None:
        super().__init__(f"A customer with email {email} is already registered")


class DeviceNotFoundError(DomainError):
    def __init__(self, device_id: object) -> None:
        super().__init__(f"Device {device_id} does not exist")


class SerialAlreadyRegisteredError(DomainError):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"A device with serial {serial_number} is already registered")


class DeviceInactiveError(DomainError):
    def __init__(self, device_id: object) -> None:
        super().__init__(f"Device {device_id} is inactive and cannot ingest measurements")


class BatchRejectedError(DomainError):
    """Every measurement in the batch was rejected; carries the per-item results."""

    def __init__(self, result: BatchResult) -> None:
        super().__init__(
            f"{result.count(ItemStatus.REJECTED)} of {len(result.items)} measurements were rejected"
        )
        self.result = result
