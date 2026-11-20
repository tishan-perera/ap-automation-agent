"""FastAPI entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, init_db
from app.logging_setup import configure_logging
from app.routers import invoices
from app.services.seed import seed_reference_data

logger = logging.getLogger(__name__)


def seed_on_startup(data_dir: Path) -> None:
    """Insert missing sample POs, receipts, and GL config. Existing rows are never changed."""
    try:
        with Session(get_engine()) as session:
            created = seed_reference_data(session, data_dir)
    except FileNotFoundError as exc:
        logger.warning("seed_skipped", extra={"reason": str(exc)})
        return
    logger.info("seed_complete", extra={"rows_created": created})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    seed_on_startup(Path(settings.data_dir))
    logger.info("app_started", extra={"provider": settings.llm_provider})
    yield


app = FastAPI(title="AP Automation Agent", lifespan=lifespan)
app.include_router(invoices.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}
