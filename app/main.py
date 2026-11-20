"""FastAPI entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("app_started", extra={"provider": settings.llm_provider})
    yield


app = FastAPI(title="AP Automation Agent", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_settings().llm_provider}
