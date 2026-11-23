"""FastAPI entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, init_db
from app.logging_setup import configure_logging
from app.routers import ar, export, invoices
from app.services.demo import bootstrap_ar_demo, bootstrap_demo
from app.web import routes as web_routes

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web" / "static"


def load_demo_data(settings) -> None:
    """Seed missing reference rows; ingest AP fixtures only into an empty database;
    invoice any AR sales order that has no invoice yet."""
    try:
        with Session(get_engine()) as session:
            created = bootstrap_demo(session, settings.data_dir, settings.invoices_dir)
            ar_created = bootstrap_ar_demo(session, settings.ar_data_dir, settings.ar_invoices_dir)
    except FileNotFoundError as exc:
        logger.warning("demo_data_skipped", extra={"reason": str(exc)})
        return
    logger.info(
        "demo_data_ready", extra={"invoices_created": created, "ar_invoices_created": ar_created}
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    load_demo_data(settings)
    logger.info("app_started", extra={"provider": settings.llm_provider})
    yield


app = FastAPI(title="AP Automation Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(invoices.router)
app.include_router(export.router)
app.include_router(ar.router)
app.include_router(web_routes.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}
