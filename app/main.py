import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

try:
    from app.agent_cli_mcp import build_agent, sport_server, filesystem_server, sqlite_server
    from agents import Runner
except Exception as e:
    print("Failed to import from agent_cli_mcp / agents:", e, file=sys.stderr)
    raise

load_dotenv()

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@asynccontextmanager
async def lifespan(server: FastAPI):
    """Startup/shutdown lifecycle for connecting & cleaning up MCP servers."""
    server.state.agent = None
    try:
        agent = await build_agent()
        server.state.agent = agent
        yield
    finally:
        # Graceful cleanup of any MCP servers started inside build_agent()
        try:
            for server in (sport_server, sqlite_server, filesystem_server):
                if server is not None:
                    try:
                        await asyncio.wait_for(server.cleanup(), timeout=5)
                    except Exception as exception:
                        print(f"[mcp] cleanup error: {exception}", file=sys.stderr)
            print("[mcp] disconnected")
        except Exception as exception:
            print(f"[shutdown] error: {exception}", file=sys.stderr)

app = FastAPI(
    title="Agent HTTP Server (FastAPI + OpenAI Agents SDK)",
    version="1.0.0",
    lifespan=lifespan,
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message must be non-empty")
    agent = getattr(app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="agent is not ready")
    try:
        result = await Runner.run(agent, req.message.strip())
        reply = result.final_output or ""
        return ChatResponse(reply=reply)
    except Exception as exception:
        raise HTTPException(status_code=500, detail=f"Agent error: {exception}")