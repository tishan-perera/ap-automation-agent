"""JSON API for AR invoices. The branded Receivables tab comes in a later phase."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models_ar import ARInvoice
from app.schemas_ar import ARInvoiceDetail, ARInvoiceSummary

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
