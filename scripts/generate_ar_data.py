"""Generate synthetic AR fixtures: customers, sales orders, planned invoices, bank payments,
and a ground-truth answer key for cash application.

Every customer, email, and amount is fictional. Emails use the reserved .example domain.
The same seed always produces byte-identical files. No PDFs here (that is Phase 10).

Usage: python scripts/generate_ar_data.py [output_dir] [--seed N]   (default: data/ar, 42)
"""

import argparse
import json
import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from pathlib import Path

CENTS = Decimal("0.01")
BASE_DATE = date(2026, 8, 3)
NET_DAYS = 30
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "ar"

# (name, account number, billing email or None, how the name appears on bank lines)
CUSTOMERS = [
    ("Amberfield Clinics LLC", "C-1001", "billing@amberfield-clinics.example", "AMBERFIELD CLINICS LLC"),
    ("Blue Harbor Retail Group", "C-1002", "ap@blueharbor-retail.example", "BLUE HARBOR RETAIL GROUP"),
    ("Pinecrest Learning Co", "C-1003", None, "PINECREST LEARNING CO"),
    ("Riverstone Facilities LLC", "C-1004", "accounts@riverstone-facilities.example", "RIVERSTONE FACILITIES"),
    ("Cedar & Elm Wholesale", "C-1005", "payables@cedar-elm.example", "CEDAR AND ELM WHOLESALE"),
    ("Northlake Studio LLC", "C-1006", "studio@northlake.example", "NORTHLAKE STUDIO LLC"),
    ("Harborview Dental Group", "C-1007", None, "HARBORVIEW DENTAL GRP"),
    ("Maple & Pine Logistics", "C-1008", "finance@maple-pine.example", "MAPLE PINE LOGISTICS"),
]  # fmt: skip

# What our fictional company sells: (description, unit price)
CATALOG = [
    ("Managed IT support, monthly", "850.00"),
    ("Onsite training session", "1200.00"),
    ("Software license, annual seat", "240.00"),
    ("Hardware maintenance visit", "375.00"),
    ("Network security review", "1650.00"),
    ("Help desk hours, block of 10", "650.00"),
    ("Laptop setup and imaging", "95.00"),
    ("Cloud backup, monthly", "180.00"),
]
ORDERS_PER_CUSTOMER = 3


def money(value) -> Decimal:
    return Decimal(value).quantize(CENTS)


def build_customers() -> list[dict]:
    return [
        {
            "account_number": account,
            "name": name,
            "billing_email": email,
            "payment_terms": f"Net {NET_DAYS}",
            "notes": "Synthetic demo customer.",
        }
        for name, account, email, _ in CUSTOMERS
    ]


def build_orders(rng: random.Random) -> list[dict]:
    drafts = []
    for c, (_, account, _, _) in enumerate(CUSTOMERS):
        for k in range(ORDERS_PER_CUSTOMER):
            lines = []
            for description, price in rng.sample(CATALOG, rng.randint(1, 3)):
                qty = rng.randint(1, 6)
                lines.append(
                    {
                        "description": description,
                        "quantity": qty,
                        "unit_price": money(price),
                        "amount": money(Decimal(price) * qty),
                    }
                )
            drafts.append(
                {
                    "customer_account": account,
                    "order_date": BASE_DATE + timedelta(days=k * 21 + c * 2),
                    "currency": "USD",
                    "status": "fulfilled",
                    "expected_total": money(sum(line["amount"] for line in lines)),
                    "line_items": lines,
                }
            )
    drafts.sort(key=lambda o: (o["order_date"], o["customer_account"]))
    return [{"order_number": f"SO-{5001 + i}", **order} for i, order in enumerate(drafts)]


def plan_invoices(orders: list[dict]) -> list[dict]:
    """One planned invoice per order, numbered AR-1001 upward in order-date order."""
    return [
        {
            "invoice_number": f"AR-{1001 + i}",
            "sales_order_number": order["order_number"],
            "customer_account": order["customer_account"],
            "invoice_date": order["order_date"],
            "due_date": order["order_date"] + timedelta(days=NET_DAYS),
            "currency": "USD",
            "subtotal": order["expected_total"],
            "tax": money(0),  # v1: synthetic orders carry no tax rate
            "total": order["expected_total"],
        }
        for i, order in enumerate(orders)
    ]


class PaymentBook:
    """Builds bank payment lines together with their answer-key records."""

    def __init__(self, invoices: list[dict]) -> None:
        self.by_customer: dict[str, list[dict]] = {}
        for inv in invoices:
            self.by_customer.setdefault(inv["customer_account"], []).append(inv)
        self.payments: list[dict] = []
        self.truth: dict[str, dict] = {}

    def inv(self, c: int, k: int) -> dict:
        return self.by_customer[CUSTOMERS[c][1]][k]

    def in_date_order(self) -> tuple[list[dict], dict[str, dict]]:
        """Sort the feed by payment date (like a real bank feed) and renumber BP-0001 up."""
        ordered = sorted(self.payments, key=lambda p: (p["payment_date"], p["payment_ref"]))
        payments, truth = [], {}
        for i, payment in enumerate(ordered, start=1):
            ref = f"BP-{i:04d}"
            truth[ref] = self.truth[payment["payment_ref"]]
            payments.append({**payment, "payment_ref": ref})
        return payments, truth

    def add(self, case_type, status, kind, amount, memo, payer, paid_on, applications, account):
        ref = f"BP-{len(self.payments) + 1:04d}"
        amount = money(amount)
        applied = money(sum((a["amount"] for a in applications), Decimal(0)))
        self.payments.append(
            {
                "payment_ref": ref,
                "payment_date": paid_on,
                "amount": amount,
                "currency": "USD",
                "memo": memo,
                "payer_name_raw": payer,
            }
        )
        self.truth[ref] = {
            "case_type": case_type,
            "expected_status": status,
            "expected_match_kind": kind,
            "expected_customer_account": account,
            "applications": applications,
            "unapplied_amount": amount - applied,
        }

    def pay_full(self, case_type, status, kind, c, invoices, memo, days_after, extra="0.00"):
        total = money(sum(inv["total"] for inv in invoices))
        self.add(
            case_type,
            status,
            kind,
            total + money(extra),
            memo,
            CUSTOMERS[c][3],
            invoices[0]["invoice_date"] + timedelta(days=days_after),
            [{"invoice_number": inv["invoice_number"], "amount": inv["total"]} for inv in invoices],
            CUSTOMERS[c][1],
        )


def build_payments(invoices: list[dict]) -> PaymentBook:
    b = PaymentBook(invoices)

    def num(c: int, k: int) -> str:  # invoice number of customer c's k-th invoice
        return b.inv(c, k)["invoice_number"]

    def digits(c: int, k: int) -> str:  # "AR-1006" -> "1006"
        return num(c, k).split("-")[1]

    # Exact invoice number in the memo, full amount: confident, should auto-post.
    exact_memos = ["Payment for {n}", "{n}", "INVOICE {n}", "Remittance {n}", "Pay {n} via ACH"]
    for c, template in enumerate(exact_memos):
        b.pay_full("exact_invoice_number", "posted", "invoice_number", c, [b.inv(c, 0)],
                   template.format(n=num(c, 0)), days_after=21 + c)  # fmt: skip

    # Messy memos: typo, number without prefix, truncated number. A careful person still
    # finds one invoice (by number, or by exact amount for that customer).
    b.pay_full("messy_memo", "posted", "invoice_number", 5, [b.inv(5, 0)],
               f"Thanks so much! AR{digits(5, 0)}", days_after=27)  # fmt: skip
    b.pay_full("messy_memo", "posted", "invoice_number", 6, [b.inv(6, 0)],
               f"inv {digits(6, 0)} thx", days_after=33)  # fmt: skip
    b.pay_full("messy_memo", "posted", "amount_and_customer", 7, [b.inv(7, 0)],
               f"PMT {num(7, 0)[:5]}", days_after=29)  # fmt: skip

    # Two partial payments on one invoice (a partial-pay pattern for later risk rules).
    partial_inv = b.inv(5, 1)
    for share, days in (("0.40", 30), ("0.30", 52)):
        amount = money(partial_inv["total"] * Decimal(share))
        b.add("partial", "needs_review", "partial", amount,
              f"Partial payment {partial_inv['invoice_number']}", CUSTOMERS[5][3],
              partial_inv["invoice_date"] + timedelta(days=days),
              [{"invoice_number": partial_inv["invoice_number"], "amount": amount}],
              CUSTOMERS[5][1])  # fmt: skip

    # One payment covering several invoices: a clear split (numbers listed) and an
    # unclear one (no numbers; the amount happens to equal two invoices).
    b.pay_full("multi_invoice", "posted", "split", 1, [b.inv(1, 1), b.inv(1, 2)],
               f"{num(1, 1)}, {num(1, 2)}", days_after=48)  # fmt: skip
    b.pay_full("multi_invoice", "needs_review", "split", 2, [b.inv(2, 1), b.inv(2, 2)],
               "Monthly remittance", days_after=50)  # fmt: skip

    # Overpayment: invoice paid in full plus extra that has nowhere to go.
    b.pay_full("overpayment", "needs_review", "overpayment", 3, [b.inv(3, 1)],
               f"Payment {num(3, 1)}", days_after=26, extra="25.00")  # fmt: skip

    # Unidentified: no useful memo or payer, and an amount matching nothing.
    for amount, memo, payer, day in (
        ("517.38", "", "", 70),
        ("1234.56", "ONLINE TRANSFER REF 88213", "ACH CREDIT", 85),
    ):
        b.add("unidentified", "unidentified", "unidentified", amount, memo, payer,
              BASE_DATE + timedelta(days=day), [], None)  # fmt: skip

    _check_ambiguity_guards(b)
    return b


def _check_ambiguity_guards(b: PaymentBook) -> None:
    """Fail loudly if a seed makes a 'findable' case ambiguous or an unidentified one findable."""
    for account, invs in b.by_customer.items():
        totals = Counter(inv["total"] for inv in invs)
        truncated = b.inv(7, 0)
        if account == truncated["customer_account"]:
            assert totals[truncated["total"]] == 1, "truncated-memo amount is not unique"
    all_totals = {inv["total"] for invs in b.by_customer.values() for inv in invs}
    pair_sums = {
        a["total"] + c["total"] for invs in b.by_customer.values() for a, c in combinations(invs, 2)
    }
    unclear = b.inv(2, 1)["total"] + b.inv(2, 2)["total"]
    assert unclear not in {inv["total"] for inv in b.by_customer[CUSTOMERS[2][1]]}
    for payment in b.payments:
        if b.truth[payment["payment_ref"]]["case_type"] == "unidentified":
            assert payment["amount"] not in all_totals | pair_sums, "unidentified amount matches"


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def generate_ar_dataset(out_dir: Path | str = DEFAULT_OUT_DIR, seed: int = 42) -> dict:
    """Write the AR fixture files to out_dir and return the ground truth."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    orders = build_orders(rng)
    invoices = plan_invoices(orders)
    payments, truth = build_payments(invoices).in_date_order()

    write_json(out_dir / "customers.json", build_customers())
    write_json(out_dir / "sales_orders.json", orders)
    write_json(out_dir / "planned_invoices.json", invoices)
    write_json(out_dir / "bank_payments.json", payments)
    write_json(out_dir / "ground_truth.json", truth)
    return json.loads(json.dumps(truth, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", nargs="?", default=DEFAULT_OUT_DIR, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    truth = generate_ar_dataset(args.out_dir, args.seed)
    customers = json.loads((args.out_dir / "customers.json").read_text())
    orders = json.loads((args.out_dir / "sales_orders.json").read_text())
    print(f"Wrote AR fixtures to {args.out_dir}")
    print(f"  customers        {len(customers)}")
    print(f"  sales orders     {len(orders)} (planned invoices {len(orders)})")
    print(f"  bank payments    {len(truth)}")
    for case_type, count in sorted(Counter(r["case_type"] for r in truth.values()).items()):
        print(f"    {case_type:<22} {count}")


if __name__ == "__main__":
    main()
