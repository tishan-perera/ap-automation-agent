"""Populate an empty database for the demo: seed reference data, then ingest fixture PDFs."""

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.ingest import ingest_bytes
from app.services.seed import seed_reference_data

logger = logging.getLogger(__name__)


def bootstrap_demo(session: Session, data_dir: Path | str, invoices_dir: Path | str) -> int:
    """Seed missing reference rows; ingest fixtures only if there are no invoices yet.

    Returns the number of invoices created. Existing data is never changed.
    """
    seed_reference_data(session, data_dir)
    if session.scalar(select(func.count()).select_from(Invoice)):
        return 0
    paths = sorted(Path(invoices_dir).glob("*.pdf"))
    created = sum(ingest_bytes(session, p.name, p.read_bytes()).created for p in paths)
    logger.info("demo_fixtures_ingested", extra={"invoices_created": created})
    return created


def bootstrap_ar_demo(session: Session, ar_dir: Path | str, pdf_dir: Path | str) -> int:
    """Seed missing AR customers, orders, and bank payments; invoice any order without an
    invoice; then match any payment not yet matched.

    Returns the number of AR invoices created. Existing rows are never changed.
    """
    seed_ar_reference(session, ar_dir)
    created = create_invoices_from_orders(session, pdf_dir)
    matched = apply_payments(session)
    if created or matched:
        logger.info(
            "demo_ar_ready",
            extra={"ar_invoices_created": len(created), "payments_matched": len(matched)},
        )
    return len(created)
