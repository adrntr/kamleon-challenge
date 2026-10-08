import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DeviceStatus
from app.models.base import Base

_STATUS_VALUES = ", ".join(f"'{status.value}'" for status in DeviceStatus)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
        CheckConstraint("btrim(serial_number) <> ''", name="serial_number_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    serial_number: Mapped[str] = mapped_column(unique=True)
    # No default: the registration service sets the initial status.
    status: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # onupdate only covers UPDATEs issued through SQLAlchemy (see DECISIONS.md, Tables).
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
