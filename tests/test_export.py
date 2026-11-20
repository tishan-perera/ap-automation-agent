import csv
import io
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Invoice
from app.services import review
from app.services.export import HEADER, export_approved
from tests.conftest import audit_events, get_invoice

EXPECTED_HEADER = [
    "BillNo",
    "Vendor",
    "InvoiceDate",
    "DueDate",
    "Amount",
    "Account",
    "LineDescription",
    "LineAmount",
    "Currency",
    "PONumber",
    "Memo",
]


def rows_of(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


def act(engine, invoice_id, action, *args):
    """Run a review action (approve / reject / edit) in its own session."""
    with Session(engine) as session:
        getattr(review, action)(session, session.get(Invoice, invoice_id), *args)


def run_export(engine):
    with Session(engine) as session:
        return export_approved(session)


def test_header_is_quickbooks_style():
    assert HEADER == EXPECTED_HEADER


def test_no_approved_invoices_gives_header_only(engine, loaded):
    csv_text, exported = run_export(engine)

    assert csv_text.splitlines() == [",".join(EXPECTED_HEADER)]
    assert exported == []


def test_includes_approved_and_excludes_rejected(engine, loaded):
    act(engine, loaded["invoice_001.pdf"], "approve")
    act(engine, loaded["invoice_016.pdf"], "reject", "No PO on file")

    csv_text, _ = run_export(engine)

    bill_numbers = {row["BillNo"] for row in rows_of(csv_text)}
    assert bill_numbers == {"NWO-2001"}
    assert "HPC-2016" not in csv_text


def test_one_row_per_line_item(engine, loaded, ground_truth):
    act(engine, loaded["invoice_002.pdf"], "approve")  # CFS-2002 has two line items
    expected = ground_truth["invoice_002.pdf"]

    rows = rows_of(run_export(engine)[0])

    assert [row["LineDescription"] for row in rows] == [
        line["description"] for line in expected["line_items"]
    ]
    assert {row["Amount"] for row in rows} == {expected["total"]}
    assert sum(Decimal(row["LineAmount"]) for row in rows) == Decimal(expected["subtotal"])
    first = rows[0]
    assert first["Vendor"] == "Cloudforge Software LLC"
    assert first["InvoiceDate"] == "09/05/2026"  # MM/DD/YYYY, like QuickBooks
    assert first["DueDate"] == "10/05/2026"
    assert first["Account"] == "6200"
    assert first["Currency"] == "USD"
    assert first["PONumber"] == expected["po_number"]
    assert "2000 Accounts Payable" in first["Memo"]


def test_invoice_without_line_items_uses_the_total(engine, loaded):
    invoice_id = loaded["invoice_022.pdf"]  # unreadable: no line items were extracted
    corrected = {
        "vendor_name": "Cedar Utilities Inc",
        "invoice_number": "CUI-9001",
        "invoice_date": "2026-10-09",
        "total": "250.00",
    }
    act(engine, invoice_id, "edit", corrected)
    act(engine, invoice_id, "approve")

    rows = rows_of(run_export(engine)[0])

    assert len(rows) == 1
    assert rows[0]["BillNo"] == "CUI-9001"
    assert rows[0]["LineAmount"] == "250.00"
    assert rows[0]["Amount"] == "250.00"
    assert rows[0]["Account"] == "6600"  # GL re-suggested from the corrected vendor


def test_export_marks_exported_and_audits_once(engine, loaded):
    invoice_id = loaded["invoice_001.pdf"]
    act(engine, invoice_id, "approve")

    _, exported = run_export(engine)
    second_csv, second = run_export(engine)

    invoice = get_invoice(engine, invoice_id)
    assert exported == [invoice_id]
    assert invoice.status == "exported"
    assert invoice.exported_at is not None
    assert audit_events(engine, invoice_id)[-1].action == "exported"
    assert second == []  # an invoice is exported only once
    assert second_csv.splitlines() == [",".join(EXPECTED_HEADER)]


def test_text_cells_cannot_become_spreadsheet_formulas(engine, loaded):
    invoice_id = loaded["invoice_001.pdf"]
    act(engine, invoice_id, "edit", {"vendor_name": '=HYPERLINK("http://example.test")'})
    act(engine, invoice_id, "approve")

    row = rows_of(run_export(engine)[0])[0]

    assert row["Vendor"].startswith("'=")


def test_get_csv_downloads_text_csv_without_changing_anything(client, engine, loaded):
    # GET must be safe: browsers and link previews can fetch it on their own.
    act(engine, loaded["invoice_001.pdf"], "approve")

    response = client.get("/export/csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == ",".join(EXPECTED_HEADER)
    assert "NWO-2001" in response.text
    assert get_invoice(engine, loaded["invoice_001.pdf"]).status == "approved"
    assert "exported" not in [e.action for e in audit_events(engine, loaded["invoice_001.pdf"])]


def test_post_csv_exports_and_marks_invoices(client, engine, loaded):
    invoice_id = loaded["invoice_001.pdf"]
    act(engine, invoice_id, "approve")

    response = client.post("/export/csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "NWO-2001" in response.text
    assert get_invoice(engine, invoice_id).status == "exported"
    assert audit_events(engine, invoice_id)[-1].action == "exported"
    assert "NWO-2001" not in client.post("/export/csv").text  # exported only once
