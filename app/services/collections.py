"""Collection reminders: draft one per customer with past-due invoices, for a person to review.

MODULE RULE: nothing here, or anywhere in this app, sends email. There is no SMTP, no mail
API, no send function, and no "sent" status (the database refuses it). Approving a draft
only means the wording is approved; a person copies it into their own email if they choose.
tests/test_collections.py fails if a mail-sending import or a send* function appears.

Tone from the oldest past-due invoice: polite 1-30 days, firm 31-60, final 61 or more.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import ARInvoice, CollectionReminder, Customer
from app.services.ar.aging import days_past_due
from app.services.llm.factory import get_reminder_writer
from app.services.llm.reminder_writer import (
    ReminderFacts,
    ReminderLine,
    ReminderWording,
    compose_body,
    subject_for,
)
from app.services.review import DEFAULT_ACTOR, ReviewError

STATUSES = ("needs_review", "approved", "rejected")  # no "sent": nothing is ever emailed


def tone_for(oldest_days_past_due: int) -> str:
    if oldest_days_past_due <= 30:
        return "polite"
    return "firm" if oldest_days_past_due <= 60 else "final"


def _audit(session, reminder, action, actor, before=None, after=None, note=None) -> None:
    session.add(
        AuditEvent(
            subject_type="collection_reminder",
            subject_id=reminder.id,
            actor=actor,
            action=action,
            before_json=before,
            after_json=after,
            note=note,
        )
    )


def past_due_facts(session: Session, customer: Customer, as_of: date) -> ReminderFacts | None:
    invoices = session.scalars(
        select(ARInvoice)
        .where(
            ARInvoice.customer_id == customer.id,
            ARInvoice.status.in_(("open", "partial")),
            ARInvoice.balance > Decimal(0),
            ARInvoice.due_date.is_not(None),
        )
        .order_by(ARInvoice.due_date, ARInvoice.invoice_number)
    )
    lines = [
        ReminderLine(i.invoice_number, i.due_date, i.balance)
        for i in invoices
        if days_past_due(i.due_date, as_of) > 0
    ]
    if not lines:
        return None
    oldest = days_past_due(lines[0].due_date, as_of)
    return ReminderFacts(customer.name, tone_for(oldest), oldest, lines)


def _already_drafted(session: Session, customer: Customer, as_of: date) -> bool:
    """A draft still waiting for review, or any draft for this same date, blocks a new one."""
    existing = session.scalars(
        select(CollectionReminder).where(CollectionReminder.customer_id == customer.id)
    )
    return any(r.status == "needs_review" or r.as_of_date == as_of for r in existing)


def generate_reminders(session: Session, as_of: date, writer=None) -> list[CollectionReminder]:
    """Draft a reminder for every customer with past-due invoices. Returns the new drafts."""
    writer = writer or get_reminder_writer(session)
    created = []
    for customer in session.scalars(select(Customer).order_by(Customer.name)):
        facts = past_due_facts(session, customer, as_of)
        if facts is None or _already_drafted(session, customer, as_of):
            continue
        wording: ReminderWording = writer.wording(facts)
        reminder = CollectionReminder(
            customer=customer,
            as_of_date=as_of,
            tone=facts.tone,
            subject=subject_for(facts),
            body=compose_body(facts, wording),
            status="needs_review",
        )
        session.add(reminder)
        session.flush()
        _audit(
            session, reminder, "reminder_drafted", "system",
            after={"tone": facts.tone, "invoices": [line.invoice_number for line in facts.lines]},
            note=f"Drafted by the {writer.name} writer.",
        )  # fmt: skip
        created.append(reminder)
    session.commit()
    return created


def list_reminders(session: Session, status: str | None = None) -> list[CollectionReminder]:
    query = select(CollectionReminder).order_by(
        CollectionReminder.as_of_date.desc(), CollectionReminder.created_at
    )
    if status:
        query = query.where(CollectionReminder.status == status)
    return list(session.scalars(query))


def _require_waiting(reminder: CollectionReminder, action: str) -> None:
    if reminder.status != "needs_review":
        raise ReviewError(f"This reminder is {reminder.status} and cannot be {action}.")


def approve(session: Session, reminder: CollectionReminder, actor: str = DEFAULT_ACTOR) -> None:
    """Approve the wording. This does not send anything."""
    _require_waiting(reminder, "approved")
    reminder.status = "approved"
    reminder.approved_by = actor
    _audit(
        session,
        reminder,
        "reminder_approved",
        actor,
        {"status": "needs_review"},
        {"status": "approved"},
    )
    session.commit()


def reject(
    session: Session, reminder: CollectionReminder, reason: str, actor: str = DEFAULT_ACTOR
) -> None:
    _require_waiting(reminder, "rejected")
    reason = (reason or "").strip()
    if not reason:
        raise ReviewError("Please give a reason for rejecting this reminder.", status_code=400)
    reminder.status = "rejected"
    _audit(
        session, reminder, "reminder_rejected", actor,
        {"status": "needs_review"}, {"status": "rejected"}, note=reason,
    )  # fmt: skip
    session.commit()


def edit(
    session: Session,
    reminder: CollectionReminder,
    subject: str,
    body: str,
    actor: str = DEFAULT_ACTOR,
) -> None:
    _require_waiting(reminder, "edited")
    subject, body = (subject or "").strip(), (body or "").strip()
    if not subject or not body:
        raise ReviewError("The subject and body cannot be empty.", status_code=400)
    before = {"subject": reminder.subject, "body": reminder.body}
    reminder.subject, reminder.body = subject, body
    _audit(session, reminder, "reminder_edited", actor, before, {"subject": subject, "body": body})
    session.commit()
