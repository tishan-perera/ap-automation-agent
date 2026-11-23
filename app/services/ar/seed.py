"""Load synthetic AR customers and sales orders from data/ar/ JSON fixtures.

Safe to run repeatedly: rows are matched by account_number / order_number and existing
rows are never overwritten. Bank payments are loaded in a later phase.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_ar import Customer, SalesOrder, SalesOrderLineItem


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _seed_customers(session: Session, rows: list[dict]) -> int:
    existing = set(session.scalars(select(Customer.account_number)))
    new = [row for row in rows if row["account_number"] not in existing]
    for row in new:
        session.add(
            Customer(
                account_number=row["account_number"],
                name=row["name"],
                billing_email=row.get("billing_email"),
                payment_terms=row.get("payment_terms") or "Net 30",
                notes=row.get("notes"),
            )
        )
    session.flush()
    return len(new)


def _seed_orders(session: Session, rows: list[dict]) -> int:
    existing = set(session.scalars(select(SalesOrder.order_number)))
    customers = {c.account_number: c for c in session.scalars(select(Customer))}
    created = 0
    for row in rows:
        if row["order_number"] in existing:
            continue
        order = SalesOrder(
            order_number=row["order_number"],
            customer=customers[row["customer_account"]],
            order_date=date.fromisoformat(row["order_date"]),
            currency=row.get("currency", "USD"),
            status=row.get("status", "fulfilled"),
            expected_total=_decimal(row.get("expected_total")),
        )
        order.line_items = [
            SalesOrderLineItem(
                description=line.get("description"),
                quantity=_decimal(line.get("quantity")),
                unit_price=_decimal(line.get("unit_price")),
                amount=_decimal(line.get("amount")),
            )
            for line in row.get("line_items", [])
        ]
        session.add(order)
        created += 1
    return created


def seed_ar_reference(session: Session, ar_dir: Path | str) -> dict[str, int]:
    """Insert missing customers and sales orders; return how many of each were created."""
    ar_dir = Path(ar_dir)
    created = {
        "customers": _seed_customers(session, json.loads((ar_dir / "customers.json").read_text())),
        "sales_orders": _seed_orders(
            session, json.loads((ar_dir / "sales_orders.json").read_text())
        ),
    }
    session.commit()
    return created
