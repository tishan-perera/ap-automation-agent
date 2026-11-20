"""Load sample POs, receiving records, chart of accounts, and vendor GL map from data/.

Safe to run repeatedly: existing rows are left alone, so later edits are never overwritten.
The chart of accounts and vendor map are stored as JSON in app_settings (no secrets).
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AppSetting,
    POLineItem,
    PurchaseOrder,
    ReceivingLineItem,
    ReceivingRecord,
)


def _load(data_dir: Path, name: str):
    return json.loads((data_dir / name).read_text())


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _seed_pos(session: Session, pos: list[dict]) -> int:
    existing = set(session.scalars(select(PurchaseOrder.po_number)))
    created = 0
    for po in pos:
        if po["po_number"] in existing:
            continue
        row = PurchaseOrder(
            po_number=po["po_number"],
            vendor_name=po["vendor_name"],
            po_date=_date(po.get("po_date")),
            currency=po.get("currency", "USD"),
            status=po.get("status", "open"),
            expected_total=_decimal(po.get("expected_total")),
        )
        row.line_items = [
            POLineItem(
                description=line.get("description"),
                quantity=_decimal(line.get("quantity")),
                unit_price=_decimal(line.get("unit_price")),
                amount=_decimal(line.get("amount")),
            )
            for line in po.get("line_items", [])
        ]
        session.add(row)
        created += 1
    return created


def _seed_receiving(session: Session, receipts: list[dict]) -> int:
    existing = {
        tuple(row)
        for row in session.execute(
            select(ReceivingRecord.po_number, ReceivingRecord.packing_slip_number)
        )
    }
    created = 0
    for receipt in receipts:
        key = (receipt["po_number"], receipt.get("packing_slip_number"))
        if key in existing:
            continue
        row = ReceivingRecord(
            po_number=receipt["po_number"],
            received_date=_date(receipt.get("received_date")),
            packing_slip_number=receipt.get("packing_slip_number"),
        )
        row.line_items = [
            ReceivingLineItem(
                description=line.get("description"),
                quantity_received=_decimal(line.get("quantity_received")),
            )
            for line in receipt.get("line_items", [])
        ]
        session.add(row)
        created += 1
    return created


def _seed_setting(session: Session, key: str, value) -> int:
    if session.get(AppSetting, key) is not None:
        return 0
    session.add(AppSetting(key=key, value=json.dumps(value)))
    return 1


def seed_reference_data(session: Session, data_dir: Path | str) -> dict[str, int]:
    """Insert missing reference rows and return how many of each were created."""
    data_dir = Path(data_dir)
    created = {
        "purchase_orders": _seed_pos(session, _load(data_dir, "sample_pos.json")),
        "receiving_records": _seed_receiving(session, _load(data_dir, "sample_receiving.json")),
        "chart_of_accounts": _seed_setting(
            session, "chart_of_accounts", _load(data_dir, "chart_of_accounts.json")
        ),
        "vendor_gl_map": _seed_setting(
            session, "vendor_gl_map", _load(data_dir, "vendor_gl_map.json")
        ),
    }
    session.commit()
    return created
