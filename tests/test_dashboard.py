from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice
from app.services.demo import bootstrap_demo
from tests.conftest import DATA_DIR, audit_events, get_invoice


def is_html(response):
    return response.headers["content-type"].startswith("text/html")


def test_root_redirects_to_queue(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"] == "/queue"


def test_queue_lists_invoices_with_exception_text(client, loaded):
    response = client.get("/queue")

    assert response.status_code == 200
    assert is_html(response)
    assert "NWO-2001" in response.text
    assert "Invoice total is missing or unreadable." in response.text


def test_queue_filters_by_status(client, loaded):
    assert "No invoices" in client.get("/queue", params={"status": "approved"}).text
    client.post(f"/invoices/{loaded['invoice_001.pdf']}/approve")

    approved = client.get("/queue", params={"status": "approved"}).text
    assert "NWO-2001" in approved
    assert "CUI-2006" not in approved
    assert "CUI-2006" in client.get("/queue", params={"status": "all"}).text


def test_approve_sets_status_and_writes_audit(client, engine, loaded):
    invoice_id = loaded["invoice_001.pdf"]

    response = client.post(f"/invoices/{invoice_id}/approve", follow_redirects=False)

    assert response.status_code == 303
    invoice = get_invoice(engine, invoice_id)
    assert invoice.status == "approved"
    assert invoice.approved_by == "ap-specialist"
    assert invoice.approved_at is not None
    assert audit_events(engine, invoice_id)[-1].action == "approved"


def test_cannot_approve_without_a_total(client, engine, loaded):
    invoice_id = loaded["invoice_021.pdf"]  # missing_total fixture

    response = client.post(f"/invoices/{invoice_id}/approve")

    assert response.status_code == 409
    assert "total is missing" in response.text.lower()
    assert get_invoice(engine, invoice_id).status == "needs_review"


def test_reject_with_reason(client, engine, loaded):
    invoice_id = loaded["invoice_013.pdf"]

    response = client.post(
        f"/invoices/{invoice_id}/reject",
        data={"reason": "Duplicate of NWO-2001"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert get_invoice(engine, invoice_id).status == "rejected"
    event = audit_events(engine, invoice_id)[-1]
    assert event.action == "rejected"
    assert event.note == "Duplicate of NWO-2001"


def test_reject_requires_a_reason(client, engine, loaded):
    invoice_id = loaded["invoice_013.pdf"]

    response = client.post(f"/invoices/{invoice_id}/reject", data={"reason": "  "})

    assert response.status_code == 400
    assert "reason" in response.text.lower()
    assert get_invoice(engine, invoice_id).status == "needs_review"


def test_edit_total_writes_before_and_after_and_rechecks(client, engine, loaded):
    invoice_id = loaded["invoice_021.pdf"]  # missing_total fixture

    response = client.post(
        f"/invoices/{invoice_id}/edit", data={"total": "105.00"}, follow_redirects=False
    )

    assert response.status_code == 303
    invoice = get_invoice(engine, invoice_id)
    assert invoice.total == Decimal("105.00")
    assert invoice.status == "needs_review"
    assert "MISSING_TOTAL" not in [f["code"] for f in invoice.validation_flags]
    event = audit_events(engine, invoice_id)[-1]
    assert event.action == "edited"
    assert event.before_json == {"total": None}
    assert event.after_json == {"total": "105.00"}


def test_edit_vendor_reruns_po_matching(client, engine, loaded):
    invoice_id = loaded["invoice_023.pdf"]  # wrong_vendor fixture
    assert get_invoice(engine, invoice_id).match_status == "vendor_mismatch"

    client.post(f"/invoices/{invoice_id}/edit", data={"vendor_name": "Harbor and Pine Consulting"})

    invoice = get_invoice(engine, invoice_id)
    assert invoice.vendor_name == "Harbor and Pine Consulting"
    assert invoice.match_status == "matched"
    assert audit_events(engine, invoice_id)[-1].before_json == {
        "vendor_name": "Lumen and Co Marketing"
    }


def test_edit_rejects_an_invalid_amount(client, engine, loaded):
    invoice_id = loaded["invoice_021.pdf"]

    response = client.post(f"/invoices/{invoice_id}/edit", data={"total": "abc"})

    assert response.status_code == 400
    assert "not a valid amount" in response.text.lower()
    assert get_invoice(engine, invoice_id).total is None


def test_approved_invoice_cannot_be_edited(client, engine, loaded):
    invoice_id = loaded["invoice_001.pdf"]
    client.post(f"/invoices/{invoice_id}/approve")

    response = client.post(f"/invoices/{invoice_id}/edit", data={"total": "1.00"})

    assert response.status_code == 409
    assert "cannot be edited" in response.text.lower()
    # htmx keeps the address bar on the detail page, not the POST-only /edit URL.
    assert response.headers["hx-replace-url"] == f"/invoices/{invoice_id}"
    assert get_invoice(engine, invoice_id).total == Decimal("45.90")
    assert "edited" not in [e.action for e in audit_events(engine, invoice_id)]


def test_detail_shows_fields_flags_match_gl_lines_and_audit(client, loaded):
    invoice_id = loaded["invoice_018.pdf"]  # price_variance fixture
    client.post(f"/invoices/{invoice_id}/edit", data={"po_number": "PO-1013"})  # no-op edit
    client.post(f"/invoices/{invoice_id}/reject", data={"reason": "Price too high"})

    response = client.get(f"/invoices/{invoice_id}")
    text = response.text

    assert response.status_code == 200
    assert is_html(response)
    assert "NWO-2018" in text  # extracted field
    assert "PRICE_VARIANCE" in text  # flag
    assert "224.10" in text and "257.73" in text  # match details: expected vs actual
    assert "6100" in text and "Credit 2000 Accounts Payable" in text  # GL code and reason
    assert "Copy paper, case of 10 reams" in text  # line item
    assert text.index('data-action="ingested"') < text.index('data-action="rejected"')


def test_detail_unknown_invoice_is_404(client):
    assert client.get("/invoices/does-not-exist").status_code == 404


def test_settings_shows_provider_and_key_presence_but_never_the_key(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-123")
    get_settings.cache_clear()

    response = client.get("/settings")

    assert is_html(response)
    assert "mock" in response.text
    assert 'data-key="openai">yes' in response.text
    assert 'data-key="anthropic">no' in response.text
    assert "sk-test-not-a-real-key-123" not in response.text


def test_bootstrap_fills_an_empty_database_once(engine):
    with Session(engine) as session:
        first = bootstrap_demo(session, DATA_DIR, DATA_DIR / "invoices")
        second = bootstrap_demo(session, DATA_DIR, DATA_DIR / "invoices")
        total = session.scalar(select(func.count()).select_from(Invoice))

    assert first == 24
    assert second == 0
    assert total == 24


def test_queue_has_export_form_that_htmx_does_not_intercept(client, loaded):
    client.post(f"/invoices/{loaded['invoice_001.pdf']}/approve")

    html = client.get("/queue", params={"status": "approved"}).text

    # Exporting changes data, so it is a POST form (never a link a browser might prefetch).
    # hx-boost would swap the CSV into the page, so the form opts out of it.
    assert '<form method="post" action="/export/csv" hx-boost="false"' in html
    assert 'href="/export/csv"' not in html
    assert "Export 1 approved" in html


def test_exported_filter_lists_exported_invoices(client, loaded):
    client.post(f"/invoices/{loaded['invoice_003.pdf']}/approve")  # HPC-2003 is unique
    client.post("/export/csv")

    assert "HPC-2003" in client.get("/queue", params={"status": "exported"}).text
    assert "HPC-2003" not in client.get("/queue").text  # gone from Needs review
    assert "No invoices" in client.get("/queue", params={"status": "approved"}).text
