from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_now() -> datetime:
    """The current time; a dependency so tests can fix it with app.dependency_overrides."""
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(get_now)]
