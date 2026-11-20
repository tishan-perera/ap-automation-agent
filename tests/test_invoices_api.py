import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.schemas import ValidationFlag
from app.services.enrich import exception_reasons
from app.services.seed import seed_reference_data
from tests.conftest import DATA_DIR


@pytest.fixture
def client(engine):
    with Session(engine) as seed_session:
        seed_reference_data(seed_session, DATA_DIR)

    def test_session():
        with Session(engine) as db_session:
            yield db_session

    app.dependency_overrides[get_session] = test_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ingested(client):
    return {
        i["filename"]: i for i in client.post("/api/invoices/ingest-fixtures").json()["invoices"]
    }


def test_list_invoices_returns_every_ingested_invoice(client, ingested, ground_truth):
    body = client.get("/api/invoices").json()

    assert len(body) == len(ground_truth)
    first = body[0]
    for key in ("id", "status", "is_duplicate", "match_status", "gl_account", "exception_reasons"):
        assert key in first


def test_list_invoices_can_filter_by_status(client, ingested):
    assert len(client.get("/api/invoices", params={"status": "needs_review"}).json()) == 24
    assert client.get("/api/invoices", params={"status": "approved"}).json() == []


def test_invoice_detail_has_lines_match_and_gl(client, ingested, ground_truth):
    invoice_id = ingested["invoice_001.pdf"]["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()

    assert detail["invoice_number"] == ground_truth["invoice_001.pdf"]["invoice_number"]
    assert len(detail["line_items"]) == len(ground_truth["invoice_001.pdf"]["line_items"])
    assert detail["match_status"] == "matched"
    assert detail["match_type"] == "three_way"
    assert detail["gl_account"] == "6100"
    assert "Credit 2000 Accounts Payable" in detail["gl_reason"]


def test_pipeline_results_per_fixture(client, ingested):
    by_file = ingested
    assert by_file["invoice_013.pdf"]["duplicate_kind"] == "exact"
    assert by_file["invoice_015.pdf"]["duplicate_kind"] == "soft"
    assert by_file["invoice_022.pdf"]["match_status"] == "not_applicable"
    assert by_file["invoice_022.pdf"]["gl_account"] is None


def test_unknown_invoice_returns_404(client):
    assert client.get("/api/invoices/does-not-exist").status_code == 404


def test_exception_reasons_are_deduplicated():
    flags = [
        ValidationFlag(code="A", message="same"),
        ValidationFlag(code="B", message="same"),
        ValidationFlag(code="C", message="other"),
    ]
    assert exception_reasons(flags) == ["same", "other"]
