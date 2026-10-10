from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DatabaseUnavailableError


async def ping(session: AsyncSession) -> None:
    try:
        await session.execute(text("SELECT 1"))
    except OperationalError as e:
        raise DatabaseUnavailableError() from e
