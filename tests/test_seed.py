import json
from decimal import Decimal

from sqlalchemy import func, select

from app.models import AppSetting, PurchaseOrder, ReceivingRecord
from app.services.seed import seed_reference_data
from tests.conftest import DATA_DIR


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_seed_loads_pos_receiving_and_chart(session):
    pos = json.loads((DATA_DIR / "sample_pos.json").read_text())
    receipts = json.loads((DATA_DIR / "sample_receiving.json").read_text())

    created = seed_reference_data(session, DATA_DIR)

    assert created["purchase_orders"] == len(pos) == count(session, PurchaseOrder)
    assert created["receiving_records"] == len(receipts) == count(session, ReceivingRecord)
    po = session.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001")).one()
    assert po.expected_total == Decimal(pos[0]["expected_total"])
    assert len(po.line_items) == len(pos[0]["line_items"])
    chart = json.loads(session.get(AppSetting, "chart_of_accounts").value)
    assert chart["credit_account"]["code"] == "2000"
    assert session.get(AppSetting, "vendor_gl_map") is not None


def test_seed_twice_creates_nothing_new_and_keeps_edits(session):
    seed_reference_data(session, DATA_DIR)
    po = session.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001")).one()
    po.status = "closed"  # a change made after seeding must survive a re-seed
    session.commit()

    created = seed_reference_data(session, DATA_DIR)

    assert set(created.values()) == {0}
    session.refresh(po)
    assert po.status == "closed"
