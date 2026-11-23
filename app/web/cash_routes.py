"""The Cash tab: money in and money out, from existing AP and AR rows. Read-only."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.ar.aging import resolve_as_of
from app.services.cash_dashboard import build_cash
from app.web.routes import templates

router = APIRouter(include_in_schema=False)
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/cash", response_class=HTMLResponse)
def cash_page(request: Request, session: SessionDep, as_of: date | None = None):
    cash = build_cash(session, resolve_as_of(as_of))
    return templates.TemplateResponse(request, "cash.html", {"cash": cash})
