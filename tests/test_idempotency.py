import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.models import Invoice, ProcessedFile
from app.services.ingest import ingest_bytes


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def client(engine):
    def test_session():
        with Session(engine) as db_session:
            yield db_session

    app.dependency_overrides[get_session] = test_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_same_bytes_twice_returns_same_invoice(session, fixture_pdf):
    path, _ = fixture_pdf("happy")
    data = path.read_bytes()

    first = ingest_bytes(session, "first.pdf", data)
    second = ingest_bytes(session, "renamed-copy.pdf", data)

    assert first.invoice.id == second.invoice.id
    assert first.created is True
    assert second.created is False
    assert count(session, Invoice) == 1
    assert count(session, ProcessedFile) == 1


def test_file_hash_is_sha256_of_bytes(session, fixture_pdf):
    path, _ = fixture_pdf("happy")
    data = path.read_bytes()

    result = ingest_bytes(session, path.name, data)

    assert result.invoice.file_hash == hashlib.sha256(data).hexdigest()
    assert session.get(ProcessedFile, result.invoice.file_hash).invoice_id == result.invoice.id


def test_upload_endpoint_is_idempotent(client, session, fixture_pdf):
    path, expected = fixture_pdf("happy")
    files = {"file": (path.name, path.read_bytes(), "application/pdf")}

    first = client.post("/api/invoices/upload", files=files)
    second = client.post("/api/invoices/upload", files=files)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["invoice_number"] == expected["invoice_number"]
    assert second.json()["already_processed"] is True
    assert count(session, Invoice) == 1


def test_upload_rejects_non_pdf(client):
    files = {"file": ("notes.txt", b"hello", "text/plain")}
    assert client.post("/api/invoices/upload", files=files).status_code == 400


def test_ingest_fixtures_processes_each_pdf_once(client, session, ground_truth):
    first = client.post("/api/invoices/ingest-fixtures").json()
    second = client.post("/api/invoices/ingest-fixtures").json()

    assert first["processed"] == len(ground_truth)
    assert first["created"] == len(ground_truth)
    assert second["created"] == 0
    assert count(session, Invoice) == len(ground_truth)
