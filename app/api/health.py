from fastapi import APIRouter

from app.api.dependencies import SessionDep
from app.api.problems import PROBLEM_RESPONSE
from app.schemas.health import HealthRead
from app.services import health as health_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> HealthRead:
    """The process responds. Never touches the database."""
    return HealthRead(status="ok")


@router.get("/ready", responses={503: PROBLEM_RESPONSE})
async def ready(session: SessionDep) -> HealthRead:
    """The database answers SELECT 1."""
    await health_service.check_database(session)
    return HealthRead(status="ok")
