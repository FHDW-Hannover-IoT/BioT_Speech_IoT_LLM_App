"""
app/main.py
-----------
FastAPI entry point for the BioT Sensor Assistant.

On startup:
  1. Starts the MCP sensor server as a subprocess (port 8002)
  2. Initialises the SensorAgent (LLM + MCP client)
  3. Starts the MQTT subscriber in a background thread (writes live data to SQLite)

Endpoints:
    GET  /health  — liveness check
    POST /chat    — natural language query to the agent

Run with:
    uv run python -m app.main
"""

import subprocess
import sys
import time
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent import SensorAgent
from app.logger import get_logger
from config.settings import settings
from mcp_server.mqtt_subscriber import SensorMqttSubscriber

log = get_logger(__name__)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# ── Factories ─────────────────────────────────────────────────────────────────

def _create_agent() -> SensorAgent:
    return SensorAgent(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        db_path=settings.sqlite_db_path,
        mcp_server_url=settings.mcp_server_url,
    )


def _create_subscriber() -> SensorMqttSubscriber:
    return SensorMqttSubscriber(
        broker_host=settings.mqtt_broker_host,
        broker_port=settings.mqtt_broker_port,
        db_path=settings.sqlite_db_path,
    )


def _start_mcp_server() -> subprocess.Popen:
    """
    Launch the MCP sensor server as a subprocess.
    It runs independently on MCP_SERVER_PORT and the agent talks to it via HTTP.
    """
    log.info("Starting MCP sensor server on port %d", settings.mcp_server_port)

    # No stdout/stderr redirect — MCP server output prints directly to this terminal
    # so any import errors or crashes are immediately visible.
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.sensor_mcp_server"],
    )

    # Give the process a moment to start before probing
    time.sleep(3.0)

    # Check if the process already crashed before we even started probing
    if proc.poll() is not None:
        log.error(
            "MCP server process exited immediately (exit code %d). "
            "Check the output above for the error.",
            proc.returncode,
        )
        raise RuntimeError(
            f"MCP server failed to start (exit code {proc.returncode}). "
            "See terminal output for details."
        )

    # Wait up to 15 seconds for the MCP server to become reachable
    mcp_url = f"http://localhost:{settings.mcp_server_port}/mcp"
    for attempt in range(15):
        time.sleep(1.0)

        # Check if it crashed during startup
        if proc.poll() is not None:
            log.error("MCP server crashed during startup (exit code %d)", proc.returncode)
            raise RuntimeError("MCP server crashed. See terminal output for the traceback.")

        try:
            httpx.get(mcp_url, timeout=2.0)
            log.info("MCP server ready at %s", mcp_url)
            return proc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            log.debug(
                "MCP server not ready yet (attempt %d/15): %s — %s",
                attempt + 1, type(exc).__name__, exc,
            )
        except httpx.HTTPStatusError as exc:
            # Any HTTP response (even 4xx) means the server is up
            log.info(
                "MCP server ready at %s (HTTP %s — expected for MCP protocol)",
                mcp_url, exc.response.status_code,
            )
            return proc

    log.warning(
        "MCP server did not respond within 18 seconds — "
        "agent will still work but first tool call may be slow"
    )
    return proc



# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting BioT Sensor Assistant — %s", settings)

    # 1. Start MCP server subprocess
    app.state.mcp_proc = _start_mcp_server()

    # 2. Start LLM agent (connects to MCP server via HTTP)
    app.state.agent = _create_agent()
    log.info(
        "Agent ready (provider=%s, model=%s, mcp=%s)",
        settings.llm_provider, settings.llm_model, settings.mcp_server_url,
    )

    # 3. Start MQTT subscriber (writes live sensor data to SQLite)
    app.state.subscriber = _create_subscriber()
    app.state.subscriber.start()
    log.info(
        "MQTT subscriber running (broker=%s:%d)",
        settings.mqtt_broker_host, settings.mqtt_broker_port,
    )

    yield

    # Shutdown
    log.info("Server shutting down")
    if hasattr(app.state, "subscriber"):
        app.state.subscriber.stop()
    if hasattr(app.state, "mcp_proc"):
        app.state.mcp_proc.terminate()
        log.info("MCP server process terminated")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BioT Sensor Assistant",
    description="LLM-powered IoT sensor assistant for the BioT Speech IoT project.",
    version="2.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc, exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Check server logs."},
    )


def get_agent() -> SensorAgent:
    return app.state.agent


@app.get("/health", summary="Liveness check")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    agent: Annotated[SensorAgent, Depends(get_agent)],
):
    message = req.message.strip()
    if not message:
        log.warning("Rejected empty message")
        raise HTTPException(status_code=400, detail="message must not be empty")

    log.info("Request received: %r", message[:120])
    try:
        reply = agent.run(message)
        log.info("Response sent: %r", reply[:120])
        return ChatResponse(reply=reply)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Agent error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Agent error. Check server logs.")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting uvicorn on %s:%d", settings.server_host, settings.server_port)
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
    )