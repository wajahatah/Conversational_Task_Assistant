"""Conversational Task Assistant — FastAPI Application Entry Point."""

from __future__ import annotations

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

logger = structlog.get_logger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info(
        "Starting Conversational Task Assistant",
        env=settings.app_env,
        llm_provider=settings.llm_provider,
        telegram_mode="polling" if settings.telegram_use_polling else "webhook",
    )
    yield
    logger.info("Shutting down Conversational Task Assistant")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Conversational Task Assistant",
    description="A progress-aware conversational task assistant for Telegram.",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routes ───────────────────────────────────────────────────────────────────

from app.handlers.webhook_handler import router as webhook_router  # noqa: E402
app.include_router(webhook_router, tags=["Webhook"])


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
    }


# ─── Root ─────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
async def root():
    return {"message": "Conversational Task Assistant is running."}
