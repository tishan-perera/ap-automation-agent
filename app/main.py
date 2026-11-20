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
from app.routers import export, invoices
from app.services.demo import bootstrap_demo
from app.web import routes as web_routes

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web" / "static"


def load_demo_data(data_dir: Path, invoices_dir: Path) -> None:
    """Seed missing reference rows; ingest fixtures only into an empty database."""
    try:
        with Session(get_engine()) as session:
            created = bootstrap_demo(session, data_dir, invoices_dir)
    except FileNotFoundError as exc:
        logger.warning("demo_data_skipped", extra={"reason": str(exc)})
        return
    logger.info("demo_data_ready", extra={"invoices_created": created})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    load_demo_data(Path(settings.data_dir), Path(settings.invoices_dir))
    logger.info("app_started", extra={"provider": settings.llm_provider})
    yield


app = FastAPI(title="AP Automation Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(invoices.router)
app.include_router(export.router)
app.include_router(web_routes.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}
