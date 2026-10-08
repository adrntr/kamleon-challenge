"""Database helpers shared by conftest and the migration tests."""

import asyncio
from collections.abc import Awaitable
from typing import Any

from alembic.config import Config
from psycopg.errors import DuplicateDatabase
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import ProgrammingError


def create_database(url: str) -> None:
    """Create the database if it does not exist, through the maintenance database.

    CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    database = make_url(url).database
    maintenance = create_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    except ProgrammingError as e:
        if not isinstance(e.orig, DuplicateDatabase):
            raise
    finally:
        maintenance.dispose()


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    return config


def sync_engine(url: str) -> Engine:
    return create_engine(url, connect_args={"options": "-c timezone=UTC"})


async def request_during_uncommitted_insert[T](
    db: Engine,
    insert: str,
    params: dict[str, Any],
    request: Awaitable[T],
    *,
    commit: bool,
) -> T:
    """Force the unique-conflict interleaving deterministically.

    A runs `insert` on its own connection without committing. B (`request`, normally an API
    call) runs as a background task and blocks on A's uncommitted row. Once B is seen waiting
    on a lock, A commits or rolls back according to `commit`, and B's result is returned.
    """
    task = asyncio.ensure_future(request)
    try:
        with db.connect() as connection_a:
            connection_a.execute(text(insert), params)
            for _ in range(100):
                # A new connection per poll: pg_stat_activity is a snapshot per transaction.
                with db.connect() as observer:
                    waiting = observer.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                        )
                    ).scalar_one()
                if waiting:
                    break
                await asyncio.sleep(0.05)
            assert waiting, "B never blocked on A's uncommitted insert"
            assert not task.done()
            if commit:
                connection_a.commit()
            else:
                connection_a.rollback()
    except BaseException:
        task.cancel()
        raise
    return await task
