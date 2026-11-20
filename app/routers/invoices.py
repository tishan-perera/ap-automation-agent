"""JSON API for invoices: upload, ingest fixtures, list, and detail."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Invoice
from app.schemas import FixtureIngestSummary, InvoiceDetail, InvoiceSummary
from app.services.ingest import IngestResult, ingest_bytes

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

SessionDep = Annotated[Session, Depends(get_session)]


def _summary(result: IngestResult) -> InvoiceSummary:
    summary = InvoiceSummary.model_validate(result.invoice)
    summary.already_processed = not result.created
    return summary


@router.post("/upload", response_model=InvoiceSummary)
async def upload_invoice(
    file: UploadFile, response: Response, session: SessionDep
) -> InvoiceSummary:
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a PDF file.")
    result = ingest_bytes(session, Path(file.filename or "upload.pdf").name, data)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return _summary(result)


@router.post("/ingest-fixtures", response_model=FixtureIngestSummary)
def ingest_fixtures(session: SessionDep) -> FixtureIngestSummary:
    paths = sorted(Path(get_settings().invoices_dir).glob("*.pdf"))
    results = [ingest_bytes(session, path.name, path.read_bytes()) for path in paths]
    return FixtureIngestSummary(
        processed=len(results),
        created=sum(result.created for result in results),
        invoices=[_summary(result) for result in results],
    )


@router.get("", response_model=list[InvoiceSummary])
def list_invoices(session: SessionDep, status: str | None = None) -> list[Invoice]:
    query = select(Invoice).order_by(Invoice.created_at, Invoice.filename)
    if status:
        query = query.where(Invoice.status == status)
    return list(session.scalars(query))


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: str, session: SessionDep) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found.")
    return invoice
