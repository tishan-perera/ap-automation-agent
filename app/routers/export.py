"""CSV download of approved invoices.

GET is read-only, because browsers, link previews, and crawlers may fetch GET URLs on their
own. Only POST marks invoices exported, so a stray request can never "use up" an export.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.export import export_approved, preview_csv

router = APIRouter(tags=["export"])
SessionDep = Annotated[Session, Depends(get_session)]


def _csv_download(csv_text: str, prefix: str) -> Response:
    filename = f"{prefix}-{datetime.now(UTC):%Y%m%d-%H%M%S}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/csv")
def download_approved_csv(session: SessionDep) -> Response:
    """Download approved invoices as CSV without changing anything (a preview)."""
    return _csv_download(preview_csv(session), "ap-approved-preview")


@router.post("/export/csv")
def export_csv(session: SessionDep) -> Response:
    """Export approved invoices: download the CSV and mark them exported (only once)."""
    csv_text, _ = export_approved(session)
    return _csv_download(csv_text, "ap-export")
