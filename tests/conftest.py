import pytest
from sqlalchemy.orm import Session

from app.db import init_db, make_engine


@pytest.fixture
def engine(tmp_path):
    """A fresh SQLite database file per test, created with init_db()."""
    db_engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(db_engine)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as db_session:
        yield db_session
