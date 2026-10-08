from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import health as health_repository


async def check_database(session: AsyncSession) -> None:
    """Raise DatabaseUnavailableError if the database does not answer."""
    await health_repository.ping(session)
