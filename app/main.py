from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import health
from app.api.problems import register_error_handlers
from app.config import Settings, get_settings
from app.db import create_engine
from app.logging_config import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory, run with `uvicorn app.main:create_app --factory`."""
    settings = settings or get_settings()
    configure_logging()
    engine = create_engine(str(settings.database_url))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="Kamleon devices API", lifespan=lifespan)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    register_error_handlers(app)
    app.include_router(health.router)
    return app
