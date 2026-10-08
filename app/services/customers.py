import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import CustomerNotFoundError
from app.models import Customer
from app.repositories import customers as customer_repository


async def register_customer(session: AsyncSession, *, name: str, email: str) -> Customer:
    """Emails are case-insensitive: stored in lowercase (docs/DECISIONS.md, Customer email)."""
    async with session.begin():
        return await customer_repository.create(session, name=name, email=email.lower())


async def get_customer(session: AsyncSession, customer_id: uuid.UUID) -> Customer:
    async with session.begin():
        customer = await customer_repository.get(session, customer_id)
    if customer is None:
        raise CustomerNotFoundError(customer_id)
    return customer
