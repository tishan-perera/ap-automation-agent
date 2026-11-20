"""FastAPI entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.logging_setup import configure_logging
from app.schemas import FixtureIngestSummary, InvoiceSummary
from app.services.ingest import IngestResult, ingest_bytes

logger = logging.getLogger(__name__)

SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    logger.info("app_started", extra={"provider": settings.llm_provider})
    yield


app = FastAPI(title="AP Automation Agent", lifespan=lifespan)


def _summary(result: IngestResult) -> InvoiceSummary:
    summary = InvoiceSummary.model_validate(result.invoice)
    summary.already_processed = not result.created
    return summary


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}


@app.post("/api/invoices/upload", response_model=InvoiceSummary)
async def upload_invoice(
    file: UploadFile, response: Response, session: SessionDep
) -> InvoiceSummary:
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a PDF file.")
    result = ingest_bytes(session, Path(file.filename or "upload.pdf").name, data)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return _summary(result)


@app.post("/api/invoices/ingest-fixtures", response_model=FixtureIngestSummary)
def ingest_fixtures(session: SessionDep) -> FixtureIngestSummary:
    paths = sorted(Path(get_settings().invoices_dir).glob("*.pdf"))
    results = [ingest_bytes(session, path.name, path.read_bytes()) for path in paths]
    return FixtureIngestSummary(
        processed=len(results),
        created=sum(result.created for result in results),
        invoices=[_summary(result) for result in results],
    )
