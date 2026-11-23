"""SQLite engine, session factory, and table creation."""

import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models_ar  # noqa: F401  (registers the AR tables on Base.metadata)
from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


def _ensure_sqlite_dir(engine: Engine) -> None:
    url = make_url(str(engine.url))
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def _missing_columns(engine: Engine) -> dict[str, list[str]]:
    """Columns the models expect but an existing (older) table does not have."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = {}
    for table in Base.metadata.sorted_tables:
        if table.name in existing_tables:
            have = {c["name"] for c in inspector.get_columns(table.name)}
            lacking = [c.name for c in table.columns if c.name not in have]
            if lacking:
                missing[table.name] = lacking
    return missing


def init_db(engine: Engine | None = None) -> None:
    """Create the data directory (for file-based SQLite) and any missing tables.

    create_all adds new tables but never alters existing ones. If a local database is
    older than the models, log how to fix it: delete data/app.db (gitignored) and restart.
    """
    engine = engine or get_engine()
    _ensure_sqlite_dir(engine)
    Base.metadata.create_all(engine)
    missing = _missing_columns(engine)
    if missing:
        logger.warning(
            "db_schema_outdated",
            extra={
                "missing_columns": missing,
                "fix": "Delete data/app.db (synthetic demo data only) and restart the app.",
            },
        )


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a database session."""
    session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with session_factory() as session:
        yield session
