"""Generate the synthetic AR invoice PDFs and an index from data/ar/ fixtures.

Uses the app's own invoicing service on a throwaway in-memory database, so the files are
exactly what the app produces. Output is deterministic: re-running gives the same files.

Usage: python scripts/generate_ar_invoices.py [ar_dir] [out_dir]
       (defaults: data/ar and data/ar/invoices)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # lets `python scripts/generate_ar_invoices.py` import the app

from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference

DEFAULT_AR_DIR = ROOT / "data" / "ar"
DEFAULT_OUT_DIR = DEFAULT_AR_DIR / "invoices"


def _amount(value) -> str | None:
    return None if value is None else str(value)


def generate_ar_invoice_files(ar_dir: Path | str, out_dir: Path | str) -> list[dict]:
    """Write one PDF per invoiced order plus index.json to out_dir. Returns the index."""
    ar_dir, out_dir = Path(ar_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("AR-*.pdf"):
        stale.unlink()

    engine = make_engine("sqlite://")  # in memory; nothing touches data/app.db
    init_db(engine)
    with Session(engine) as session:
        seed_ar_reference(session, ar_dir)
        invoices = create_invoices_from_orders(session, out_dir)
        index = [
            {
                "filename": Path(inv.pdf_path).name,
                "invoice_number": inv.invoice_number,
                "sales_order_number": inv.sales_order_number,
                "customer_account": inv.customer_account,
                "customer_name": inv.customer_name,
                "invoice_date": inv.invoice_date.isoformat(),
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "payment_terms": inv.payment_terms,
                "subtotal": _amount(inv.subtotal),
                "tax": _amount(inv.tax),
                "total": _amount(inv.total),
                "status": inv.status,
            }
            for inv in invoices
        ]
    engine.dispose()
    (out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main(argv: list[str]) -> None:
    ar_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_AR_DIR
    out_dir = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT_DIR
    index = generate_ar_invoice_files(ar_dir, out_dir)
    numbers = [entry["invoice_number"] for entry in index]
    statuses = {}
    for entry in index:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    print(f"Wrote {len(index)} AR invoice PDFs and index.json to {out_dir}")
    if numbers:
        print(f"  numbers  {numbers[0]} to {numbers[-1]}")
    print(f"  statuses {statuses}")


if __name__ == "__main__":
    main(sys.argv)
