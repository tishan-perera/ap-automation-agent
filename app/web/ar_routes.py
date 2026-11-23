"""Simple AR HTML pages: payment review, aging, risk flags, and reminder drafts.

Plain on purpose: the branded Receivables tab is built in Phase 15.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models_ar import BankPayment
from app.routers.ar import payment_or_404, reminder_or_404
from app.services import collections
from app.services.ar import payment_review
from app.services.ar.aging import BUCKETS, build_aging, resolve_as_of
from app.services.ar.risk import INVOICES_30_PLUS_CAP, POINTS, assess_risk
from app.services.review import ReviewError
from app.web.routes import templates

router = APIRouter(include_in_schema=False)
SessionDep = Annotated[Session, Depends(get_session)]

NOTICES = {
    "approved": "Payment posted to its invoices.",
    "rejected": "Match rejected. The payment is now unidentified and invoices are unchanged.",
    "edited": "Applications updated. Review them, then approve to post.",
}
SHOWN = ("needs_review", "unidentified", "posted")


def parse_lines(text: str) -> list[tuple[str, str]]:
    """'AR-1011 1920.00' per line -> [('AR-1011', '1920.00')]. Validation happens in the service."""
    rows = []
    for line in text.splitlines():
        parts = line.replace(",", " ").split()
        if parts:
            if len(parts) != 2:
                raise ReviewError(
                    f"Use one 'invoice amount' pair per line, e.g. AR-1011 1920.00 (got {line!r}).",
                    400,
                )
            rows.append((parts[0], parts[1]))
    return rows


def _page(request, session, notice=None, error=None, status_code=200):
    payments = session.scalars(
        select(BankPayment).order_by(BankPayment.payment_date, BankPayment.payment_ref)
    ).all()
    groups = {key: [p for p in payments if p.status == key] for key in SHOWN}
    context = {"groups": groups, "notice": NOTICES.get(notice or ""), "error": error}
    response = templates.TemplateResponse(
        request, "ar_payments.html", context, status_code=status_code
    )
    response.headers["HX-Replace-Url"] = "/receivables/payments"
    return response


@router.get("/receivables/payments", response_class=HTMLResponse)
def payments_page(request: Request, session: SessionDep, notice: str | None = None):
    return _page(request, session, notice=notice)


def _act(request, session, payment_ref, notice, action, *args):
    payment = payment_or_404(session, payment_ref)
    try:
        action(session, payment, *args)
    except ReviewError as exc:
        session.rollback()
        return _page(request, session, error=exc.message, status_code=exc.status_code)
    url = f"/receivables/payments?notice={notice}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/receivables/payments/{payment_ref}/approve")
def approve(request: Request, payment_ref: str, session: SessionDep):
    return _act(request, session, payment_ref, "approved", payment_review.approve)


@router.post("/receivables/payments/{payment_ref}/reject")
def reject(
    request: Request, payment_ref: str, session: SessionDep, reason: Annotated[str, Form()] = ""
):
    return _act(request, session, payment_ref, "rejected", payment_review.reject, reason)


@router.post("/receivables/payments/{payment_ref}/edit")
def edit(
    request: Request, payment_ref: str, session: SessionDep, lines: Annotated[str, Form()] = ""
):
    try:
        rows = parse_lines(lines)
    except ReviewError as exc:
        return _page(request, session, error=exc.message, status_code=exc.status_code)
    return _act(request, session, payment_ref, "edited", payment_review.edit, rows)


@router.get("/receivables/aging", response_class=HTMLResponse)
def aging_page(request: Request, session: SessionDep, as_of: date | None = None):
    """Read-only: viewing the report never changes an invoice or writes an audit row."""
    report = build_aging(session, resolve_as_of(as_of))
    context = {"report": report, "buckets": BUCKETS}
    return templates.TemplateResponse(request, "ar_aging.html", context)


@router.get("/receivables/risk", response_class=HTMLResponse)
def risk_page(request: Request, session: SessionDep, as_of: date | None = None):
    """Read-only: computed on demand from the rules; nothing is saved."""
    as_of = resolve_as_of(as_of)
    context = {
        "results": assess_risk(session, as_of),
        "as_of": as_of,
        "points": POINTS,
        "invoices_30_plus_cap": INVOICES_30_PLUS_CAP,
    }
    return templates.TemplateResponse(request, "ar_risk.html", context)


REMINDER_NOTICES = {
    "generated": "Drafts created for customers with past-due invoices. Nothing was emailed.",
    "approved": "Wording approved. Nothing was emailed; copy it into your own email if you want to send it.",
    "rejected": "Draft rejected.",
    "edited": "Draft updated. Review it, then approve or reject.",
}


def _reminders_page(request, session, notice=None, error=None, status_code=200):
    reminders = collections.list_reminders(session)
    groups = {s: [r for r in reminders if r.status == s] for s in collections.STATUSES}
    context = {"groups": groups, "notice": REMINDER_NOTICES.get(notice or ""), "error": error,
               "as_of": resolve_as_of()}  # fmt: skip
    response = templates.TemplateResponse(
        request, "ar_reminders.html", context, status_code=status_code
    )
    response.headers["HX-Replace-Url"] = "/receivables/reminders"
    return response


@router.get("/receivables/reminders", response_class=HTMLResponse)
def reminders_page(request: Request, session: SessionDep, notice: str | None = None):
    return _reminders_page(request, session, notice=notice)


@router.post("/receivables/reminders/generate")
def generate_reminders(
    request: Request, session: SessionDep, as_of: Annotated[date | None, Form()] = None
):
    collections.generate_reminders(session, resolve_as_of(as_of))
    return RedirectResponse(
        "/receivables/reminders?notice=generated", status_code=status.HTTP_303_SEE_OTHER
    )


def _reminder_act(request, session, reminder_id, notice, action, *args):
    reminder = reminder_or_404(session, reminder_id)
    try:
        action(session, reminder, *args)
    except ReviewError as exc:
        session.rollback()
        return _reminders_page(request, session, error=exc.message, status_code=exc.status_code)
    url = f"/receivables/reminders?notice={notice}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/receivables/reminders/{reminder_id}/approve")
def approve_reminder(request: Request, reminder_id: str, session: SessionDep):
    return _reminder_act(request, session, reminder_id, "approved", collections.approve)


@router.post("/receivables/reminders/{reminder_id}/reject")
def reject_reminder(
    request: Request, reminder_id: str, session: SessionDep, reason: Annotated[str, Form()] = ""
):
    return _reminder_act(request, session, reminder_id, "rejected", collections.reject, reason)


@router.post("/receivables/reminders/{reminder_id}/edit")
def edit_reminder(
    request: Request, reminder_id: str, session: SessionDep,
    subject: Annotated[str, Form()] = "", body: Annotated[str, Form()] = "",
):  # fmt: skip
    return _reminder_act(request, session, reminder_id, "edited", collections.edit, subject, body)
