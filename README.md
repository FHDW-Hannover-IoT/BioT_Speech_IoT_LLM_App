# FastAPI Agent HTTP Server

Minimal HTTP wrapper around the OpenAI Agents setup in `app/agent_cli_mcp.py`.  
Expose a simple `/chat` endpoint to send a text message and receive the agent reply.

## Features
- Reuses `build_agent()` from `app/agent_cli_mcp.py` so CLI and server share the same wiring.
- Lightweight FastAPI + Uvicorn server.
- Configurable via environment variables.

## Requirements
- Python 3.10+ (project uses Poetry-style `pyproject.toml`)
- `uv` (uv tools) or `uvicorn`
- Set `OPENAI` credentials and other env vars (see below)

## Quick start (using `uv`)
1. Create a virtual env (optional):
   - `uv venv`  
   - Activate: `. .venv/bin/activate` (Linux/macOS) or `.venv\Scripts\Activate.ps1` (Windows PowerShell)
2. Install deps:
   - `uv sync`
3. Configure environment:
   - Copy and edit `.env.example`: `cp .env.example .env`
   - Set at least `OPENAI_API_KEY`
4. Run the server locally:
   - Loopback only: `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8001`
   - Bind all interfaces: `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8001`
   - Specify LAN IP: `uv run uvicorn app.main:app --host 192.168.1.50 --port 8001`

Production-ish:
- Multiple workers: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4`  
  Note: each worker is a separate process and gets its own `app.state`. Share cross-process state using DB/Redis/etc.

## Endpoints
- `GET /health` → `{ "status": "ok" }`
- `POST /chat` with JSON:
  - Request: `{ "message": "Hello" }`
  - Response: `{ "reply": "..." }`

Example:
- `curl -X POST "http://127.0.0.1:8001/chat" -H "Content-Type: application/json" -d '{ "message": "Hi!" }'`

## Important env vars
Place in `.env` or set in your environment:
- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional)
- `MCP_FS_ROOTS` (optional)
- `SQLITE_DB_PATH` (optional — e.g., `data/demo.db`)
- `SPORT_MCP_URL` (if using a streamable HTTP MCP)

## Project layout
- `app/` — FastAPI app and agent wiring
  - `app/main.py` — FastAPI application entry
  - `app/agent_cli_mcp.py` — exposes `build_agent()` and MCP wiring
  - `app/client.py`, `app/client_direct.py`, `app/agent_cli_mcp.py`
- `mcp_server/` — example MCP integrations (e.g., `dice_and_sport.py`)
- `data/` — sample SQLite DB (`data/demo.db`)
- `sample_files/` — supporting files

## Troubleshooting
- `ModuleNotFoundError: No module named 'app.main'`
  - Run from project root and ensure `app/__init__.py` exists.
- Port in use:
  - Change `--port` or stop the conflicting process.
- Agent/MCP errors:
  - Verify env vars and that the MCP service is reachable.
  - If your MCP runs on `http://localhost:8000/mcp`, run this server on a different port (e.g., `8001`).

## Windows firewall / Linux UFW
- Windows: allow the chosen port or the uvicorn process when prompted.
- Ubuntu UFW: `sudo ufw allow 8001`

## Notes
- When running multiple workers, persist shared state externally (DB, Redis).
- The server intentionally reuses CLI agent code so behavior matches your CLI runs.
