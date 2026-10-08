from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


def create_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Engine shared by the API and Alembic.

    The session time zone is UTC regardless of the server configuration, and
    statement parameters are kept out of exception messages, since they can
    contain health data.
    """
    return create_async_engine(
        url,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
        hide_parameters=True,
        **kwargs,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services open and commit the transactions."""
    async with request.app.state.sessionmaker() as session:
        yield session
