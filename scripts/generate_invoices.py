"""Generate a synthetic AP dataset: invoice PDFs, POs, receiving records, and ground truth.

Every vendor, item, and number here is fictional. Output is deterministic, so re-running
produces byte-identical PDFs.

Usage: python scripts/generate_invoices.py [output_dir]   (default: data/)
"""

import json
import random
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CENTS = Decimal("0.01")
BASE_DATE = date(2026, 9, 1)
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data"

CHART_OF_ACCOUNTS = {
    "credit_account": {"code": "2000", "name": "Accounts Payable"},
    "expense_accounts": [
        {"code": "6100", "name": "Office Supplies"},
        {"code": "6200", "name": "Software and Subscriptions"},
        {"code": "6300", "name": "Professional Services"},
        {"code": "6400", "name": "Facilities and Rent"},
        {"code": "6500", "name": "Travel and Meals"},
        {"code": "6600", "name": "Utilities"},
        {"code": "6700", "name": "Marketing"},
    ],
}

# name -> (invoice number prefix, tax rate, [(item description, unit price), ...])
VENDORS = {
    "Northwind Office Supply LLC": (
        "NWO",
        "0.08",
        [("Copy paper, case of 10 reams", "42.50"), ("Ballpoint pens, box of 50", "18.75")],
    ),
    "Cloudforge Software LLC": (
        "CFS",
        "0",
        [("Project tracker seat, monthly", "12.00"), ("Cloud storage 1 TB, monthly", "45.00")],
    ),
    "Harbor and Pine Consulting": (
        "HPC",
        "0",
        [("Process review, hourly", "150.00"), ("Workshop facilitation, half day", "900.00")],
    ),
    "Riverview Facilities Group": (
        "RFG",
        "0.08",
        [("Janitorial service, monthly", "1200.00"), ("HVAC filter replacement", "85.00")],
    ),
    "Lumen and Co Marketing": (
        "LCM",
        "0",
        [("Social campaign management", "1500.00"), ("Brochure design", "650.00")],
    ),
    "Cedar Utilities Inc": (
        "CUI",
        "0",
        [("Electricity service, monthly", "780.40"), ("Water and sewer, monthly", "132.15")],
    ),
}
VENDOR_NAMES = list(VENDORS)


def money(value) -> Decimal:
    return Decimal(value).quantize(CENTS)


def make_lines(vendor: str, qtys: list[int], price_factor: str = "1") -> list[dict]:
    items = VENDORS[vendor][2]
    lines = []
    for (description, price), qty in zip(items, qtys, strict=False):
        if qty:
            unit_price = money(Decimal(price) * Decimal(price_factor))
            lines.append(
                {
                    "description": description,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "amount": money(unit_price * qty),
                }
            )
    return lines


def totals(vendor: str, lines: list[dict]) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = money(sum((line["amount"] for line in lines), Decimal(0)))
    tax = money(subtotal * Decimal(VENDORS[vendor][1]))
    return subtotal, tax, subtotal + tax


class DatasetBuilder:
    def __init__(self) -> None:
        self.invoices: list[dict] = []
        self.pos: list[dict] = []
        self.receiving: list[dict] = []
        self._day = 0

    def next_date(self) -> date:
        self._day += 2
        return BASE_DATE + timedelta(days=self._day)

    def add_po(self, vendor: str, lines: list[dict], po_date: date) -> str:
        po_number = f"PO-{1001 + len(self.pos)}"
        self.pos.append(
            {
                "po_number": po_number,
                "vendor_name": vendor,
                "po_date": po_date,
                "currency": "USD",
                "status": "open",
                "expected_total": totals(vendor, lines)[2],
                "line_items": lines,
            }
        )
        return po_number

    def add_receiving(self, po_number: str, lines: list[dict], received: date, short_by=0):
        self.receiving.append(
            {
                "po_number": po_number,
                "received_date": received,
                "packing_slip_number": f"PS-{5001 + len(self.receiving)}",
                "line_items": [
                    {
                        "description": line["description"],
                        "quantity_received": line["quantity"] - short_by,
                    }
                    for line in lines
                ],
            }
        )

    def add_invoice(self, case_type: str, vendor: str | None, lines: list[dict], **fields):
        invoice_date = fields.pop("invoice_date", None) or self.next_date()
        record = {
            "filename": f"invoice_{len(self.invoices) + 1:03d}.pdf",
            "case_type": case_type,
            "vendor_name": vendor,
            "invoice_number": None,
            "invoice_date": invoice_date,
            "due_date": invoice_date + timedelta(days=30),
            "po_number": None,
            "currency": "USD",
            "subtotal": None,
            "tax": None,
            "total": None,
            "line_items": lines,
            "duplicate_of": None,
        }
        if vendor is None:  # unreadable: nothing can be read, so every field is null
            record.update(invoice_date=None, due_date=None)
        else:
            subtotal, tax, total = totals(vendor, lines)
            seq = 2001 + len(self.invoices)
            record.update(
                invoice_number=f"{VENDORS[vendor][0]}-{seq}",
                subtotal=subtotal,
                tax=tax,
                total=total,
            )
        record.update(fields)
        self.invoices.append(record)
        return record

    def add_po_invoice(self, case_type, vendor, lines, po_lines=None, po_vendor=None, **fields):
        """Add an invoice backed by a PO (PO lines default to the invoice lines)."""
        invoice_date = self.next_date()
        po_number = self.add_po(
            po_vendor or vendor, po_lines or lines, invoice_date - timedelta(days=7)
        )
        return self.add_invoice(
            case_type, vendor, lines, invoice_date=invoice_date, po_number=po_number, **fields
        )


def build_dataset() -> DatasetBuilder:
    b = DatasetBuilder()

    happy = []
    for i in range(12):
        vendor = VENDOR_NAMES[i % len(VENDOR_NAMES)]
        lines = make_lines(vendor, [i % 4 + 1, i % 3])
        record = b.add_po_invoice("happy", vendor, lines)
        if i % 2 == 0:
            b.add_receiving(record["po_number"], lines, record["invoice_date"] - timedelta(days=2))
        happy.append(record)

    copied = ("vendor_name", "invoice_number", "invoice_date", "po_number", "line_items")
    original = happy[0]
    for _ in range(2):
        b.add_invoice(
            "duplicate_exact",
            original["vendor_name"],
            original["line_items"],
            **{key: original[key] for key in copied},
            duplicate_of=original["filename"],
        )

    soft = happy[1]
    b.add_invoice(
        "duplicate_soft",
        soft["vendor_name"],
        soft["line_items"],
        invoice_date=soft["invoice_date"] + timedelta(days=2),
        po_number=soft["po_number"],
        duplicate_of=soft["filename"],
    )

    for vendor in (VENDOR_NAMES[2], VENDOR_NAMES[4]):
        b.add_invoice("missing_po", vendor, make_lines(vendor, [2, 1]))

    for vendor in (VENDOR_NAMES[0], VENDOR_NAMES[3]):
        po_lines = make_lines(vendor, [4, 2])
        b.add_po_invoice("price_variance", vendor, make_lines(vendor, [4, 2], "1.15"), po_lines)

    qty_lines = make_lines(VENDOR_NAMES[0], [10, 0])
    record = b.add_po_invoice("qty_variance", VENDOR_NAMES[0], qty_lines)
    b.add_receiving(record["po_number"], qty_lines, record["invoice_date"], short_by=2)

    # The Total line is left off the PDF, so ground truth must be null, never 0.
    b.add_po_invoice(
        "missing_total", VENDOR_NAMES[1], make_lines(VENDOR_NAMES[1], [5, 1]), total=None
    )

    b.add_invoice("unreadable", None, [])

    wrong_lines = make_lines(VENDOR_NAMES[4], [1, 0])
    b.add_po_invoice("wrong_vendor", VENDOR_NAMES[4], wrong_lines, po_vendor=VENDOR_NAMES[2])

    mismatch_lines = make_lines(VENDOR_NAMES[5], [1, 1])
    record = b.add_po_invoice("line_math_mismatch", VENDOR_NAMES[5], mismatch_lines)
    record["total"] += Decimal("50.00")  # printed total is wrong on purpose
    return b


def fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def render_invoice(path: Path, inv: dict) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(72, 740, "INVOICE")
    pdf.setFont("Helvetica", 11)
    header = [
        ("Vendor", inv["vendor_name"]),
        ("Invoice Number", inv["invoice_number"]),
        ("Invoice Date", inv["invoice_date"].isoformat()),
        ("Due Date", inv["due_date"].isoformat()),
        ("PO Number", inv["po_number"]),
        ("Bill To", "Example Buyer Co (fictional)"),
    ]
    y = 710
    for label, value in header:
        if value:
            pdf.drawString(72, y, f"{label}: {value}")
            y -= 16

    y -= 20
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(72, y, "Description")
    for x, title in ((380, "Qty"), (460, "Unit Price"), (540, "Amount")):
        pdf.drawRightString(x, y, title)
    pdf.setFont("Helvetica", 11)
    for line in inv["line_items"]:
        y -= 18
        pdf.drawString(72, y, line["description"])
        pdf.drawRightString(380, y, str(line["quantity"]))
        pdf.drawRightString(460, y, fmt_money(line["unit_price"]))
        pdf.drawRightString(540, y, fmt_money(line["amount"]))

    y -= 30
    for label in ("Subtotal", "Tax", "Total"):
        value = inv[label.lower()]
        if value is not None:  # missing_total omits the Total line entirely
            pdf.drawRightString(460, y, f"{label}:")
            pdf.drawRightString(540, y, fmt_money(value))
            y -= 16

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(72, 60, "Synthetic invoice for demo purposes. All names are fictional.")
    # A per-file reference keeps every PDF's bytes unique, so a resent (duplicate) invoice
    # is a different file with the same invoice data, like a real vendor resend.
    pdf.drawString(72, 48, f"Document ref: {Path(inv['filename']).stem}")
    pdf.save()


def render_unreadable(path: Path) -> None:
    """Scrambled characters only. The charset has no t/o/a/l, so 'total' can never appear."""
    rng = random.Random(42)
    charset = "#%&*+=?~^|<>{}[]0123456789xqzjkvwbYRGHMN"
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setFont("Courier", 10)
    for row in range(30):
        pdf.drawString(72, 740 - row * 18, "".join(rng.choice(charset) for _ in range(70)))
    pdf.save()


def ground_truth_record(inv: dict) -> dict:
    return {key: value for key, value in inv.items() if key != "filename"}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def generate_dataset(out_dir: Path | str = DEFAULT_OUT_DIR) -> dict:
    """Write PDFs to out_dir/invoices/ and JSON files to out_dir/. Returns the ground truth."""
    out_dir = Path(out_dir)
    invoice_dir = out_dir / "invoices"
    invoice_dir.mkdir(parents=True, exist_ok=True)
    for stale in invoice_dir.glob("invoice_*.pdf"):
        stale.unlink()

    builder = build_dataset()
    for inv in builder.invoices:
        path = invoice_dir / inv["filename"]
        if inv["case_type"] == "unreadable":
            render_unreadable(path)
        else:
            render_invoice(path, inv)

    ground_truth = {inv["filename"]: ground_truth_record(inv) for inv in builder.invoices}
    write_json(out_dir / "ground_truth.json", ground_truth)
    write_json(out_dir / "sample_pos.json", builder.pos)
    write_json(out_dir / "sample_receiving.json", builder.receiving)
    write_json(out_dir / "chart_of_accounts.json", CHART_OF_ACCOUNTS)
    return json.loads(json.dumps(ground_truth, default=str))


def main(argv: list[str]) -> None:
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT_DIR
    ground_truth = generate_dataset(out_dir)
    counts = Counter(rec["case_type"] for rec in ground_truth.values())
    print(f"Wrote {len(ground_truth)} invoice PDFs to {out_dir / 'invoices'}")
    for case_type, count in sorted(counts.items()):
        print(f"  {case_type:<20} {count}")


if __name__ == "__main__":
    main(sys.argv)
