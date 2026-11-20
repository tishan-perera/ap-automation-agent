"""SQLite engine, session factory, and table creation."""

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


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


def init_db(engine: Engine | None = None) -> None:
    """Create the data directory (for file-based SQLite) and all tables."""
    engine = engine or get_engine()
    _ensure_sqlite_dir(engine)
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a database session."""
    session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with session_factory() as session:
        yield session
