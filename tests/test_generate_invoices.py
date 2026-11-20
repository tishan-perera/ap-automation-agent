import hashlib
import json
from collections import Counter

import pdfplumber
import pytest

from scripts.generate_invoices import generate_dataset

FICTIONAL_VENDORS = {
    "Northwind Office Supply LLC",
    "Cloudforge Software LLC",
    "Harbor and Pine Consulting",
    "Riverview Facilities Group",
    "Lumen and Co Marketing",
    "Cedar Utilities Inc",
}

REQUIRED_KEYS = {
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "po_number",
    "subtotal",
    "tax",
    "total",
    "line_items",
    "case_type",
}

MIN_CASE_COUNTS = {
    "happy": 10,
    "duplicate_exact": 2,
    "duplicate_soft": 1,
    "missing_po": 2,
    "price_variance": 2,
    "qty_variance": 1,
    "missing_total": 1,
    "unreadable": 1,
    "wrong_vendor": 1,
    "line_math_mismatch": 1,
}


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("dataset")
    generate_dataset(out_dir)
    return out_dir


@pytest.fixture(scope="module")
def ground_truth(dataset_dir):
    return json.loads((dataset_dir / "ground_truth.json").read_text())


def load_json(dataset_dir, name):
    return json.loads((dataset_dir / name).read_text())


def pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def first_of(ground_truth, case_type):
    return next((name, rec) for name, rec in ground_truth.items() if rec["case_type"] == case_type)


def test_generates_between_20_and_30_pdfs(dataset_dir):
    pdfs = list((dataset_dir / "invoices").glob("*.pdf"))
    assert 20 <= len(pdfs) <= 30


def test_ground_truth_has_one_record_per_pdf(dataset_dir, ground_truth):
    pdf_names = {p.name for p in (dataset_dir / "invoices").glob("*.pdf")}
    assert set(ground_truth) == pdf_names
    for record in ground_truth.values():
        assert REQUIRED_KEYS <= set(record)


def test_case_type_minimum_counts(ground_truth):
    counts = Counter(rec["case_type"] for rec in ground_truth.values())
    for case_type, minimum in MIN_CASE_COUNTS.items():
        assert counts[case_type] >= minimum, case_type


def test_vendors_are_fictional_and_have_no_email(dataset_dir, ground_truth):
    vendors = [rec["vendor_name"] for rec in ground_truth.values()]
    vendors += [po["vendor_name"] for po in load_json(dataset_dir, "sample_pos.json")]
    for vendor in vendors:
        if vendor is None:
            continue
        assert "@" not in vendor
        assert vendor in FICTIONAL_VENDORS


def test_happy_invoices_reference_existing_pos(dataset_dir, ground_truth):
    po_numbers = {po["po_number"] for po in load_json(dataset_dir, "sample_pos.json")}
    happy = [rec for rec in ground_truth.values() if rec["case_type"] == "happy"]
    assert happy
    for record in happy:
        assert record["po_number"] in po_numbers


def test_qty_variance_invoices_have_receiving_records(dataset_dir, ground_truth):
    received = {r["po_number"] for r in load_json(dataset_dir, "sample_receiving.json")}
    qty = [rec for rec in ground_truth.values() if rec["case_type"] == "qty_variance"]
    assert qty
    for record in qty:
        assert record["po_number"] in received


def test_chart_of_accounts_is_written(dataset_dir):
    chart = load_json(dataset_dir, "chart_of_accounts.json")
    assert chart["credit_account"]["code"] == "2000"
    codes = {account["code"] for account in chart["expense_accounts"]}
    assert codes == {"6100", "6200", "6300", "6400", "6500", "6600", "6700"}


def test_happy_pdf_text_has_vendor_and_invoice_number(dataset_dir, ground_truth):
    filename, record = first_of(ground_truth, "happy")
    text = pdf_text(dataset_dir / "invoices" / filename)
    assert record["vendor_name"] in text
    assert record["invoice_number"] in text


def test_missing_total_is_null_not_zero(dataset_dir, ground_truth):
    filename, record = first_of(ground_truth, "missing_total")
    assert record["total"] is None
    text = pdf_text(dataset_dir / "invoices" / filename)
    assert "Total:" not in text


def test_unreadable_pdf_has_no_labeled_total(dataset_dir, ground_truth):
    filename, _record = first_of(ground_truth, "unreadable")
    text = pdf_text(dataset_dir / "invoices" / filename)
    assert text.strip()
    assert "total" not in text.lower()


def test_every_pdf_has_unique_bytes(dataset_dir):
    # Exact duplicates must be different files with the same invoice data. Byte-identical
    # copies would be skipped by hash idempotency and never reach duplicate detection.
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset_dir.glob("**/*.pdf")]
    assert len(hashes) == len(set(hashes))
