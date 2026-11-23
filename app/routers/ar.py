"""JSON API for AR invoices. The branded Receivables tab comes in a later phase."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models_ar import ARInvoice, BankPayment
from app.schemas_ar import ARInvoiceDetail, ARInvoiceSummary, BankPaymentOut, EditIn, RejectIn
from app.services.ar import payment_review
from app.services.review import ReviewError

router = APIRouter(prefix="/api/ar", tags=["accounts receivable"])
SessionDep = Annotated[Session, Depends(get_session)]


def _invoice_or_404(session: Session, invoice_number: str) -> ARInvoice:
    invoice = session.scalars(
        select(ARInvoice).where(ARInvoice.invoice_number == invoice_number)
    ).first()
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AR invoice not found.")
    return invoice


@router.get("/invoices", response_model=list[ARInvoiceSummary])
def list_ar_invoices(session: SessionDep) -> list[ARInvoice]:
    return list(session.scalars(select(ARInvoice).order_by(ARInvoice.invoice_number)))


@router.get("/invoices/{invoice_number}", response_model=ARInvoiceDetail)
def get_ar_invoice(invoice_number: str, session: SessionDep) -> ARInvoice:
    return _invoice_or_404(session, invoice_number)


@router.get("/invoices/{invoice_number}/pdf")
def get_ar_invoice_pdf(invoice_number: str, session: SessionDep) -> FileResponse:
    invoice = _invoice_or_404(session, invoice_number)
    if not invoice.pdf_path or not Path(invoice.pdf_path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF not found for this invoice.")
    return FileResponse(
        invoice.pdf_path, media_type="application/pdf", filename=f"{invoice_number}.pdf"
    )


# --- Bank payments and the cash-application review queue ---


def payment_or_404(session: Session, payment_ref: str) -> BankPayment:
    payment = session.scalars(
        select(BankPayment).where(BankPayment.payment_ref == payment_ref)
    ).first()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank payment not found.")
    return payment


def _review(session: Session, payment: BankPayment, action, *args) -> BankPayment:
    try:
        action(session, payment, *args)
    except ReviewError as exc:
        session.rollback()
        raise HTTPException(exc.status_code, exc.message) from None
    return payment


@router.get("/payments", response_model=list[BankPaymentOut])
def list_payments(session: SessionDep, status: str | None = None) -> list[BankPayment]:
    query = select(BankPayment).order_by(BankPayment.payment_date, BankPayment.payment_ref)
    if status:
        query = query.where(BankPayment.status == status)
    return list(session.scalars(query))


@router.get("/payments/{payment_ref}", response_model=BankPaymentOut)
def get_payment(payment_ref: str, session: SessionDep) -> BankPayment:
    return payment_or_404(session, payment_ref)


@router.post("/payments/{payment_ref}/approve", response_model=BankPaymentOut)
def approve_payment(payment_ref: str, session: SessionDep) -> BankPayment:
    return _review(session, payment_or_404(session, payment_ref), payment_review.approve)


@router.post("/payments/{payment_ref}/reject", response_model=BankPaymentOut)
def reject_payment(payment_ref: str, body: RejectIn, session: SessionDep) -> BankPayment:
    payment = payment_or_404(session, payment_ref)
    return _review(session, payment, payment_review.reject, body.reason)


@router.post("/payments/{payment_ref}/edit", response_model=BankPaymentOut)
def edit_payment(payment_ref: str, body: EditIn, session: SessionDep) -> BankPayment:
    rows = [(a.invoice_number, a.amount) for a in body.applications]
    return _review(session, payment_or_404(session, payment_ref), payment_review.edit, rows)
