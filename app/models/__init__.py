"""Importing this package registers every table on Base.metadata (used by Alembic)."""

from app.models.base import Base
from app.models.customer import Customer
from app.models.device import Device
from app.models.measurement import Measurement
from app.models.measurement_type import MeasurementType

__all__ = ["Base", "Customer", "Device", "Measurement", "MeasurementType"]
