import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest

from scripts.generate_ar_data import generate_ar_dataset

# Well-known real companies. A customer name must not contain any of these words.
REAL_COMPANY_WORDS = {
    "amazon", "walmart", "apple", "google", "alphabet", "microsoft", "target", "costco",
    "kroger", "walgreens", "cvs", "fedex", "ups", "ibm", "oracle", "intel", "cisco",
    "starbucks", "nike", "pfizer", "deloitte", "accenture", "kpmg", "mckinsey", "tesla",
    "meta", "facebook", "netflix", "disney", "verizon", "comcast", "chase", "citibank",
    "paypal", "salesforce", "adobe", "uber", "airbnb", "boeing", "toyota", "samsung",
    "homedepot", "lowes", "ikea", "quickbooks", "intuit",
}  # fmt: skip

CASE_TYPES = {
    "exact_invoice_number",
    "messy_memo",
    "partial",
    "multi_invoice",
    "overpayment",
    "unidentified",
}
MONEY = re.compile(r"^\d+\.\d{2}$")


@pytest.fixture(scope="module")
def ar_dir(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("ar")
    generate_ar_dataset(out_dir)
    return out_dir


def load(ar_dir, name):
    return json.loads((ar_dir / name).read_text())


@pytest.fixture(scope="module")
def customers(ar_dir):
    return load(ar_dir, "customers.json")


@pytest.fixture(scope="module")
def orders(ar_dir):
    return load(ar_dir, "sales_orders.json")


@pytest.fixture(scope="module")
def invoices(ar_dir):
    return {inv["invoice_number"]: inv for inv in load(ar_dir, "planned_invoices.json")}


@pytest.fixture(scope="module")
def payments(ar_dir):
    return load(ar_dir, "bank_payments.json")


@pytest.fixture(scope="module")
def truth(ar_dir):
    return load(ar_dir, "ground_truth.json")


def test_writes_all_fixture_files(ar_dir):
    for name in (
        "customers.json",
        "sales_orders.json",
        "planned_invoices.json",
        "bank_payments.json",
        "ground_truth.json",
    ):
        assert (ar_dir / name).exists(), name
    assert not list(ar_dir.glob("**/*.pdf"))  # PDFs are Phase 10


# --- Customers ---


def test_at_least_eight_fictional_customers(customers):
    names = [c["name"] for c in customers]
    assert len(set(names)) >= 8
    for name in names:
        assert "@" not in name
        words = set(re.findall(r"[a-z]+", name.lower()))
        squashed = re.sub(r"[^a-z]", "", name.lower())
        assert not words & REAL_COMPANY_WORDS, name
        assert not any(real in squashed for real in ("homedepot", "quickbooks")), name


def test_billing_emails_are_example_domain_or_null(customers):
    for customer in customers:
        email = customer["billing_email"]
        assert email is None or re.fullmatch(r"[a-z0-9.\-]+@[a-z0-9\-]+\.example", email), email
    assert any(c["billing_email"] is None for c in customers)  # some are omitted


def test_customers_have_unique_accounts_and_net_30(customers):
    accounts = [c["account_number"] for c in customers]
    assert len(accounts) == len(set(accounts))
    assert {c["payment_terms"] for c in customers} == {"Net 30"}


# --- Sales orders and planned invoices ---


def test_at_least_twenty_orders_and_repeat_customers(orders, customers):
    assert len(orders) >= 20
    per_customer = Counter(o["customer_account"] for o in orders)
    assert max(per_customer.values()) > 1
    assert set(per_customer) <= {c["account_number"] for c in customers}
    assert len({o["order_number"] for o in orders}) == len(orders)


def test_order_lines_and_totals_add_up(orders):
    for order in orders:
        assert order["line_items"]
        line_sum = Decimal(0)
        for line in order["line_items"]:
            assert MONEY.match(line["unit_price"]) and MONEY.match(line["amount"])
            assert Decimal(line["unit_price"]) * line["quantity"] == Decimal(line["amount"])
            line_sum += Decimal(line["amount"])
        assert MONEY.match(order["expected_total"])
        assert Decimal(order["expected_total"]) == line_sum


def test_planned_invoices_are_sequential_net_30_one_per_order(invoices, orders):
    numbers = sorted(invoices, key=lambda n: int(n.split("-")[1]))
    assert numbers == [f"AR-{1001 + i}" for i in range(len(orders))]
    by_order = {o["order_number"]: o for o in orders}
    assert {inv["sales_order_number"] for inv in invoices.values()} == set(by_order)
    for inv in invoices.values():
        order = by_order[inv["sales_order_number"]]
        assert inv["customer_account"] == order["customer_account"]
        assert inv["total"] == order["expected_total"]
        assert inv["tax"] == "0.00"
        issued, due = date.fromisoformat(inv["invoice_date"]), date.fromisoformat(inv["due_date"])
        assert due - issued == timedelta(days=30)


# --- Bank payments and ground truth ---


def test_every_payment_case_is_present(truth):
    counts = Counter(rec["case_type"] for rec in truth.values())
    assert set(counts) == CASE_TYPES
    assert all(counts[case] >= 1 for case in CASE_TYPES)


def test_ground_truth_has_one_record_per_payment(payments, truth):
    refs = [p["payment_ref"] for p in payments]
    assert len(refs) == len(set(refs))
    assert set(truth) == set(refs)
    for record in truth.values():
        assert record["expected_status"] in {"posted", "needs_review", "unidentified"}
        assert isinstance(record["applications"], list)


def test_money_is_decimal_text_never_float_or_zero_placeholder(payments, truth):
    for payment in payments:
        assert isinstance(payment["amount"], str) and MONEY.match(payment["amount"])
    for record in truth.values():
        for app in record["applications"]:
            assert isinstance(app["amount"], str) and MONEY.match(app["amount"])
            assert Decimal(app["amount"]) > 0
        assert isinstance(record["unapplied_amount"], str)


def test_applications_reconcile(payments, truth, invoices):
    applied_per_invoice = defaultdict(Decimal)
    for payment in payments:
        record = truth[payment["payment_ref"]]
        applied = sum((Decimal(a["amount"]) for a in record["applications"]), Decimal(0))
        assert applied + Decimal(record["unapplied_amount"]) == Decimal(payment["amount"])
        for app in record["applications"]:
            assert app["invoice_number"] in invoices  # never an invented invoice
            applied_per_invoice[app["invoice_number"]] += Decimal(app["amount"])
    for number, applied in applied_per_invoice.items():
        assert applied <= Decimal(invoices[number]["total"])


def test_unidentified_payments_have_no_customer_and_no_applications(payments, truth, invoices):
    unidentified = [p for p in payments if truth[p["payment_ref"]]["case_type"] == "unidentified"]
    assert unidentified
    balances = {Decimal(inv["total"]) for inv in invoices.values()}
    for payment in unidentified:
        record = truth[payment["payment_ref"]]
        assert record["applications"] == []
        assert record["expected_customer_account"] is None  # never invent a customer
        assert record["expected_status"] == "unidentified"
        assert Decimal(payment["amount"]) not in balances


def test_case_specific_expectations(payments, truth, invoices):
    by_case = defaultdict(list)
    for payment in payments:
        by_case[truth[payment["payment_ref"]]["case_type"]].append(
            (payment, truth[payment["payment_ref"]])
        )

    for payment, record in by_case["exact_invoice_number"]:
        (app,) = record["applications"]
        assert app["invoice_number"] in payment["memo"]
        assert record["expected_status"] == "posted"

    messy = [payment["memo"] for payment, _ in by_case["messy_memo"]]
    assert all(not re.search(r"\bAR-\d{4}\b", memo) for memo in messy)  # never a clean number

    for _, record in by_case["partial"]:
        (app,) = record["applications"]
        assert Decimal(app["amount"]) < Decimal(invoices[app["invoice_number"]]["total"])
        assert record["expected_status"] == "needs_review"

    assert any(len(r["applications"]) >= 2 for _, r in by_case["multi_invoice"])

    for payment, record in by_case["overpayment"]:
        assert Decimal(record["unapplied_amount"]) > 0
        assert record["expected_status"] == "needs_review"


def test_same_seed_is_deterministic(tmp_path):
    generate_ar_dataset(tmp_path / "a", seed=7)
    generate_ar_dataset(tmp_path / "b", seed=7)
    for name in ("customers.json", "sales_orders.json", "bank_payments.json", "ground_truth.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_bank_feed_is_in_date_order(payments):
    dates = [p["payment_date"] for p in payments]
    assert dates == sorted(dates)
