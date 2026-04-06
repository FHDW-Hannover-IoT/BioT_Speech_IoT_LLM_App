"""
app/main.py
-----------
FastAPI entry point for the BioT Sensor Assistant HTTP server.

Endpoints:
    GET  /health  — liveness check, returns {"status": "ok"}
    POST /chat    — send a message, receive a reply from the agent

The SensorAgent is instantiated once during application startup via the
lifespan context and injected into each request via FastAPI's Depends().
No secrets are ever stored in request/response objects.

Run with:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
"""

import sys
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.agent import SensorAgent
from config.settings import settings


class ChatRequest(BaseModel):
    """Incoming chat request body."""
    message: str


class ChatResponse(BaseModel):
    """Outgoing chat response body."""
    reply: str


def _create_agent() -> SensorAgent:
    """
    Factory — builds SensorAgent from validated settings.
    Called once at startup. All secrets injected here and not touched again.
    """
    return SensorAgent(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        db_path=settings.sqlite_db_path,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build agent. Shutdown: log and exit."""
    print(f"[biot] Starting — {settings}", file=sys.stderr)
    app.state.agent = _create_agent()
    print("[biot] Agent ready", file=sys.stderr)
    yield
    print("[biot] Shutting down", file=sys.stderr)


app = FastAPI(
    title="BioT Sensor Assistant",
    description="LLM-powered IoT sensor assistant for the BioT Speech IoT project.",
    version="2.0.0",
    lifespan=lifespan,
)


def get_agent() -> SensorAgent:
    """FastAPI dependency — provides the shared SensorAgent instance."""
    return app.state.agent


@app.get("/health", summary="Liveness check")
async def health():
    """Returns OK if the server is running and the agent is initialised."""
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
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    try:
        reply = agent.run(req.message.strip())
        return ChatResponse(reply=reply)
    except Exception as exc:
        print(f"[biot] Agent error: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail="The assistant encountered an error. Check server logs.",
        )