import uuid

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import EmailAlreadyRegisteredError
from app.models import Customer


async def create(session: AsyncSession, *, name: str, email: str) -> Customer:
    """Plain INSERT: the unique constraint decides, no lookup first."""
    try:
        result = await session.execute(
            insert(Customer).values(name=name, email=email).returning(Customer)
        )
    except IntegrityError as e:
        if getattr(e.orig.diag, "constraint_name", None) == "customers_email_key":  # type: ignore[union-attr]
            raise EmailAlreadyRegisteredError(email) from e
        raise
    return result.scalar_one()


async def get(session: AsyncSession, customer_id: uuid.UUID) -> Customer | None:
    return await session.get(Customer, customer_id)
