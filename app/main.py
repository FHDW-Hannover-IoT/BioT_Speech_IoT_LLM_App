"""
app/main.py
-----------
FastAPI entry point for the BioT Sensor Assistant.

On startup:
  1. Initialises DbContext (persistent SQLite write connection, WAL mode)
  2. Creates SensorRepository (typed read/write access)
  3. Starts the MCP sensor server as a subprocess (port 8002)
  4. Initialises the SensorAgent (LLM + MCP client)
  5. Starts the MQTT subscriber in a background thread

Endpoints:
    GET  /health              — liveness check
    POST /chat                — natural-language query forwarded to the LLM agent
    GET  /data/accel          — historical accel rows for Android McpDataSyncService
    GET  /data/gyro           — historical gyro rows
    GET  /data/magnet         — historical magnet rows

Run with:
    uv run python -m app.main
"""

import asyncio
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent import SensorAgent
from app.logger import get_logger
from config.settings import settings
from database.db_context import DbContext
from database.sensor_repository import SensorRepository
from mcp_server.mqtt_subscriber import SensorMqttSubscriber

log = get_logger(__name__)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


class SensorRow(BaseModel):
    timestamp: int
    x: float
    y: float
    z: float


class SensorDataResponse(BaseModel):
    rows: list[SensorRow]


# ── Startup helpers ───────────────────────────────────────────────────────────

def _probe_api_key() -> None:
    """
    Make a real 1-token call to the configured LLM provider to confirm the
    API key is valid before accepting any requests.  Crashes with SystemExit
    on failure so the problem is obvious at startup rather than mid-demo.
    """
    provider = settings.llm_provider
    api_key  = settings.llm_api_key
    model    = settings.llm_model
    log.info("Probing API key (provider=%s, model=%s) …", provider, model)

    try:
        if provider == "anthropic":
            import anthropic as _anthropic
            _anthropic.Anthropic(api_key=api_key).messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )

        elif provider in ("openai", "deepseek"):
            from openai import OpenAI as _OpenAI
            _OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com/v1" if provider == "deepseek" else None,
            ).chat.completions.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )

        elif provider == "gemini":
            import google.generativeai as _genai
            _genai.configure(api_key=api_key)
            _genai.GenerativeModel(model).generate_content(
                "hi", generation_config={"max_output_tokens": 1},
            )

        else:
            log.warning("API key probe not implemented for provider=%r — skipping", provider)
            return

        log.info("API key OK (provider=%s, model=%s)", provider, model)

    except Exception as exc:
        log.error(
            "API key check FAILED (provider=%s, model=%s): %s\n"
            "  → Check LLM_API_KEY in .env and confirm you have access to that model.",
            provider, model, exc,
        )
        raise SystemExit(1) from exc


def _start_mcp_server() -> subprocess.Popen:
    """
    Launch the MCP sensor server as a subprocess on MCP_SERVER_PORT.
    Probes the /mcp endpoint until it responds (up to mcp_probe_attempts attempts).
    """
    log.info("Starting MCP sensor server on port %d", settings.mcp_server_port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.sensor_mcp_server"],
    )

    # Check immediately for a fast crash before we start probing
    time.sleep(0.5)
    if proc.poll() is not None:
        log.error(
            "MCP server exited immediately (exit code %d). "
            "Check the output above for the error.",
            proc.returncode,
        )
        raise RuntimeError(
            f"MCP server failed to start (exit code {proc.returncode})."
        )

    mcp_url = f"http://localhost:{settings.mcp_server_port}/mcp"
    for attempt in range(settings.mcp_probe_attempts):
        time.sleep(settings.mcp_probe_interval_secs)

        if proc.poll() is not None:
            log.error("MCP server crashed during startup (exit code %d)", proc.returncode)
            raise RuntimeError("MCP server crashed. See terminal output for the traceback.")

        try:
            httpx.get(mcp_url, timeout=settings.mcp_probe_timeout_secs)
            log.info("MCP server ready at %s (attempt %d)", mcp_url, attempt + 1)
            return proc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            log.debug(
                "MCP server not ready yet (attempt %d/%d): %s",
                attempt + 1, settings.mcp_probe_attempts, type(exc).__name__,
            )
        except httpx.HTTPStatusError as exc:
            # Any HTTP response means the server is up (MCP may return non-200 for GET)
            log.info(
                "MCP server ready at %s (HTTP %s — expected for MCP protocol)",
                mcp_url, exc.response.status_code,
            )
            return proc

    log.warning(
        "MCP server did not respond within %d attempts — "
        "agent will still work but first tool call may be slow",
        settings.mcp_probe_attempts,
    )
    return proc


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting BioT Sensor Assistant — %s", settings)

    # 0. Verify the API key is valid before starting anything else
    _probe_api_key()

    # 1. Initialise database layer (persistent write connection, WAL mode, schema)
    db_context = DbContext(settings.sqlite_db_path)
    db_context.initialize()
    app.state.db_context = db_context

    repository = SensorRepository(db_context)
    app.state.repository = repository

    # 2. Start MCP sensor server subprocess
    app.state.mcp_proc = _start_mcp_server()

    # 3. Initialise LLM agent (connects to MCP server via HTTP)
    app.state.agent = SensorAgent(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        mcp_server_url=settings.mcp_server_url,
    )
    log.info(
        "Agent ready (provider=%s, model=%s, mcp=%s)",
        settings.llm_provider, settings.llm_model, settings.mcp_server_url,
    )

    # 4. Start MQTT subscriber (writes live sensor data via SensorRepository)
    subscriber = SensorMqttSubscriber(
        broker_host=settings.mqtt_broker_host,
        broker_port=settings.mqtt_broker_port,
        repository=repository,
    )
    subscriber.start()
    app.state.subscriber = subscriber
    log.info(
        "MQTT subscriber running (broker=%s:%d)",
        settings.mqtt_broker_host, settings.mqtt_broker_port,
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Server shutting down")
    if hasattr(app.state, "subscriber"):
        app.state.subscriber.stop()
    if hasattr(app.state, "mcp_proc"):
        app.state.mcp_proc.terminate()
        log.info("MCP server process terminated")
    if hasattr(app.state, "db_context"):
        app.state.db_context.close()


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


# ── Dependency helpers ────────────────────────────────────────────────────────

def get_agent() -> SensorAgent:
    return app.state.agent


def get_repository() -> SensorRepository:
    return app.state.repository


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/health", summary="Liveness check")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse, summary="Natural-language query to the LLM agent")
async def chat(
    req: ChatRequest,
    agent: Annotated[SensorAgent, Depends(get_agent)],
):
    """
    Accepts a voice transcript or typed question from the Android app and
    returns a structured JSON action that the app parses and executes.

    The agent uses MCP tools to query live sensor data from SQLite before
    composing its response.
    """
    message = req.message.strip()
    if not message:
        log.warning("Rejected empty message")
        raise HTTPException(status_code=400, detail="message must not be empty")

    log.info("Chat request: %r", message[:120])
    try:
        # Run the synchronous LLM call in a thread pool so the event loop stays
        # free for concurrent /health checks and /data requests.
        reply = await asyncio.to_thread(agent.run, message)
        log.info("Chat response: %r", reply[:120])
        return ChatResponse(reply=reply)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Agent error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Agent error. Check server logs.")


# ── Historical data endpoints (consumed by Android McpDataSyncService) ────────
#
# Response format: {"rows": [{"timestamp": ms, "x": f, "y": f, "z": f}, ...]}
# The Android McpDataSyncService parses this exact shape.

@app.get(
    "/data/accel",
    response_model=SensorDataResponse,
    summary="Historical accelerometer data for Android sync",
)
async def data_accel(
    from_ms: int = Query(..., alias="from", description="Start timestamp (ms since epoch)"),
    to_ms:   int = Query(..., alias="to",   description="End timestamp (ms since epoch)"),
    repo:    SensorRepository = Depends(get_repository),
):
    rows = await asyncio.to_thread(repo.get_range, "accel_data", from_ms, to_ms)
    return SensorDataResponse(rows=[
        SensorRow(timestamp=r["timestamp"], x=r["accelX"], y=r["accelY"], z=r["accelZ"])
        for r in rows
    ])


@app.get(
    "/data/gyro",
    response_model=SensorDataResponse,
    summary="Historical gyroscope data for Android sync",
)
async def data_gyro(
    from_ms: int = Query(..., alias="from", description="Start timestamp (ms since epoch)"),
    to_ms:   int = Query(..., alias="to",   description="End timestamp (ms since epoch)"),
    repo:    SensorRepository = Depends(get_repository),
):
    rows = await asyncio.to_thread(repo.get_range, "gyro_data", from_ms, to_ms)
    return SensorDataResponse(rows=[
        SensorRow(timestamp=r["timestamp"], x=r["gyroX"], y=r["gyroY"], z=r["gyroZ"])
        for r in rows
    ])


@app.get(
    "/data/magnet",
    response_model=SensorDataResponse,
    summary="Historical magnetometer data for Android sync",
)
async def data_magnet(
    from_ms: int = Query(..., alias="from", description="Start timestamp (ms since epoch)"),
    to_ms:   int = Query(..., alias="to",   description="End timestamp (ms since epoch)"),
    repo:    SensorRepository = Depends(get_repository),
):
    rows = await asyncio.to_thread(repo.get_range, "magnet_data", from_ms, to_ms)
    return SensorDataResponse(rows=[
        SensorRow(timestamp=r["timestamp"], x=r["magnetX"], y=r["magnetY"], z=r["magnetZ"])
        for r in rows
    ])


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting uvicorn on %s:%d", settings.server_host, settings.server_port)
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
