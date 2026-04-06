"""
app/main.py
-----------
FastAPI entry point for the BioT Sensor Assistant HTTP server.

Endpoints:
    GET  /health  — liveness check, returns {"status": "ok"}
    POST /chat    — send a message, receive a reply from the agent

Run with:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
"""

import sys
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent import SensorAgent
from app.logger import get_logger
from config.settings import settings

log = get_logger(__name__)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# ── Agent factory ─────────────────────────────────────────────────────────────

def _create_agent() -> SensorAgent:
    """Build SensorAgent from validated settings. Called once at startup."""
    return SensorAgent(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        db_path=settings.sqlite_db_path,
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting BioT Sensor Assistant — %s", settings)
    app.state.agent = _create_agent()
    log.info("Agent ready (provider=%s, model=%s)", settings.llm_provider, settings.llm_model)
    yield
    log.info("Server shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BioT Sensor Assistant",
    description="LLM-powered IoT sensor assistant for the BioT Speech IoT project.",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Global exception handler — logs and returns clean JSON ────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Check server logs."},
    )


# ── Dependency ────────────────────────────────────────────────────────────────

def get_agent() -> SensorAgent:
    """FastAPI dependency — provides the shared SensorAgent instance."""
    return app.state.agent


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", summary="Liveness check")
async def health():
    """Returns OK if the server is running."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse, summary="Chat with the BioT assistant")
async def chat(
    req: ChatRequest,
    agent: Annotated[SensorAgent, Depends(get_agent)],
):
    """
    Process a natural language query and return the assistant's reply.
    Returns 400 for empty messages, 500 on unexpected agent errors.
    """
    message = req.message.strip()

    if not message:
        log.warning("Rejected empty message from client")
        raise HTTPException(status_code=400, detail="message must not be empty")

    log.info("Request received: %r", message[:120])

    try:
        reply = agent.run(message)
        log.info("Response sent: %r", reply[:120])
        return ChatResponse(reply=reply)

    except HTTPException:
        raise

    except Exception as exc:
        # Log the full traceback server-side, return a safe message to the caller
        log.error("Agent error for message %r: %s", message[:80], exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="The assistant encountered an error. Check server logs.",
        )