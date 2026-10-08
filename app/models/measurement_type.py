from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MeasurementType(Base):
    __tablename__ = "measurement_types"
    __table_args__ = (
        CheckConstraint("btrim(type) <> ''", name="type_not_blank"),
        CheckConstraint("btrim(unit) <> ''", name="unit_not_blank"),
    )

    type: Mapped[str] = mapped_column(primary_key=True)
    unit: Mapped[str] = mapped_column(primary_key=True)
