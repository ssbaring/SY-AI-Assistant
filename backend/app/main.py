import logging

from fastapi import FastAPI

from app.core.database import check_connection

logger = logging.getLogger(__name__)

app = FastAPI(title="SY Holdings Factory Assistant API")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        check_connection()
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("DB health check failed: %s", type(exc).__name__)
        return {"status": "error", "detail": "database unavailable"}
