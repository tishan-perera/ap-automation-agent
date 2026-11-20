import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import init_db, make_engine

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(autouse=True)
def force_mock_provider(monkeypatch):
    """Tests never call a real LLM: force the mock provider and blank every API key."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


@pytest.fixture(scope="session")
def ground_truth():
    return json.loads((DATA_DIR / "ground_truth.json").read_text())


@pytest.fixture(scope="session")
def fixture_pdf(ground_truth):
    """fixture_pdf("happy") -> (path, ground-truth record) for the first PDF of that case."""

    def find(case_type: str):
        for filename, record in ground_truth.items():
            if record["case_type"] == case_type:
                return DATA_DIR / "invoices" / filename, record
        raise LookupError(case_type)

    return find
