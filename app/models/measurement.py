import uuid
from datetime import datetime

from sqlalchemy import Double, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["type", "unit"], ["measurement_types.type", "measurement_types.unit"]
        ),
        UniqueConstraint("device_id", "device_measurement_id"),
        Index(None, "device_id", "timestamp", "type"),
    )

    # No default: the service generates it with uuid.uuid7() (DECISIONS.md, Identifiers).
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    device_measurement_id: Mapped[uuid.UUID]
    type: Mapped[str]
    unit: Mapped[str]
    value: Mapped[float] = mapped_column(Double)
    timestamp: Mapped[datetime]
