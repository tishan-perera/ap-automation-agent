import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db, make_engine
from app.main import app
from app.models import AuditEvent, Invoice
from app.services.demo import bootstrap_demo

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


@pytest.fixture
def client(engine):
    def test_session():
        with Session(engine) as db_session:
            yield db_session

    app.dependency_overrides[get_session] = test_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def loaded(engine):
    """Seed POs and ingest every fixture PDF; return {filename: invoice id}."""
    with Session(engine) as session:
        bootstrap_demo(session, DATA_DIR, DATA_DIR / "invoices")
        return {name: id_ for name, id_ in session.execute(select(Invoice.filename, Invoice.id))}


def get_invoice(engine, invoice_id):
    with Session(engine) as session:
        invoice = session.get(Invoice, invoice_id)
        session.expunge(invoice)
        return invoice


def audit_events(engine, invoice_id):
    with Session(engine) as session:
        query = select(AuditEvent).where(AuditEvent.invoice_id == invoice_id)
        events = session.scalars(query.order_by(AuditEvent.at)).all()
        session.expunge_all()
        return events
