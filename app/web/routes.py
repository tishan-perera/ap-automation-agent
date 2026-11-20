"""HTML pages: approval queue, invoice detail with review actions, and settings."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import AuditEvent, Invoice
from app.services import review
from app.services.llm.factory import provider_name

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = lambda v: "—" if v is None else f"${v:,.2f}"

router = APIRouter(include_in_schema=False)
SessionDep = Annotated[Session, Depends(get_session)]

FILTERS = {
    "needs_review": "Needs review",
    "approved": "Approved",
    "rejected": "Rejected",
    "all": "All",
}
NOTICES = {
    "approved": "Invoice approved.",
    "rejected": "Invoice rejected.",
    "edited": "Changes saved. Checks were re-run on the new values.",
    "unchanged": "No changes to save.",
}


def _invoice_or_404(session: Session, invoice_id: str) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    return invoice


def _detail(request, session, invoice, notice=None, error=None, status_code=200):
    audit = session.scalars(
        select(AuditEvent).where(AuditEvent.invoice_id == invoice.id).order_by(AuditEvent.at)
    ).all()
    context = {
        "invoice": invoice,
        "audit": audit,
        "notice": NOTICES.get(notice or ""),
        "error": error,
        "editable": invoice.status == "needs_review",
    }
    response = templates.TemplateResponse(
        request, "invoice_detail.html", context, status_code=status_code
    )
    # After a failed boosted form POST, keep the address bar on the detail page.
    response.headers["HX-Replace-Url"] = f"/invoices/{invoice.id}"
    return response


def _after_action(invoice_id: str, notice: str) -> RedirectResponse:
    url = f"/invoices/{invoice_id}?notice={notice}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/queue")


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, session: SessionDep, status: str = "needs_review"):
    if status not in FILTERS:
        status = "needs_review"
    query = select(Invoice).order_by(Invoice.created_at, Invoice.filename)
    if status != "all":
        query = query.where(Invoice.status == status)
    rows = session.execute(select(Invoice.status, func.count()).group_by(Invoice.status))
    counts = {row_status: n for row_status, n in rows}
    counts["all"] = sum(counts.values())
    context = {
        "invoices": session.scalars(query).all(),
        "filters": FILTERS,
        "active": status,
        "counts": counts,
    }
    return templates.TemplateResponse(request, "queue.html", context)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(
    request: Request, invoice_id: str, session: SessionDep, notice: str | None = None
):
    return _detail(request, session, _invoice_or_404(session, invoice_id), notice=notice)


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice(request: Request, invoice_id: str, session: SessionDep):
    invoice = _invoice_or_404(session, invoice_id)
    try:
        review.approve(session, invoice)
    except review.ReviewError as exc:
        return _detail(request, session, invoice, error=exc.message, status_code=exc.status_code)
    return _after_action(invoice_id, "approved")


@router.post("/invoices/{invoice_id}/reject")
def reject_invoice(
    request: Request, invoice_id: str, session: SessionDep, reason: Annotated[str, Form()] = ""
):
    invoice = _invoice_or_404(session, invoice_id)
    try:
        review.reject(session, invoice, reason)
    except review.ReviewError as exc:
        return _detail(request, session, invoice, error=exc.message, status_code=exc.status_code)
    return _after_action(invoice_id, "rejected")


@router.post("/invoices/{invoice_id}/edit")
async def edit_invoice(request: Request, invoice_id: str, session: SessionDep):
    invoice = _invoice_or_404(session, invoice_id)
    form = {key: str(value) for key, value in (await request.form()).items()}
    try:
        changed = review.edit(session, invoice, form)
    except review.ReviewError as exc:
        session.rollback()
        return _detail(request, session, invoice, error=exc.message, status_code=exc.status_code)
    return _after_action(invoice_id, "edited" if changed else "unchanged")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: SessionDep):
    settings = get_settings()
    # Only whether each key is set is shown. The key values never reach the template.
    keys = {
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "gemini": bool(settings.gemini_api_key),
    }
    context = {
        "provider": provider_name(session),
        "keys": keys,
        "tolerance_usd": settings.amount_tolerance_usd,
        "tolerance_pct": settings.amount_tolerance_pct,
    }
    return templates.TemplateResponse(request, "settings.html", context)
