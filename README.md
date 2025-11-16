# FastAPI Agent HTTP Server with RAG

Minimal HTTP wrapper around the OpenAI Agents setup in `app/agent_cli_mcp.py` with Retrieval-Augmented Generation (RAG) support using OpenAI's Vector Stores API.

## Features
- Reuses `build_agent()` from `app/agent_cli_mcp.py` so CLI and server share the same wiring.
- RAG integration via OpenAI Vector Stores for document-based Q&A.
- Lightweight FastAPI + Uvicorn server.
- MCP (Model Context Protocol) integrations for extensibility.
- Configurable via environment variables.

## Requirements
- Python 3.10+ (project uses `pyproject.toml`)
- `uv` (uv tools) or `uvicorn`
- Set `OPENAI_API_KEY` and other env vars (see below)

## Quick start (using `uv`)
1. Create a virtual env (optional):
   - `uv venv`
   - Activate: `. .venv/bin/activate` (Linux/macOS) or `.venv\Scripts\Activate.ps1` (Windows PowerShell)
2. Install deps:
   - `uv sync`
3. Configure environment:
   - Create `.env` file with required variables (see below)
   - Set at least `OPENAI_API_KEY`
4. Run the server locally:
   - Loopback only: `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8001`
   - Bind all interfaces: `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8001`
   - Specify LAN IP: `uv run uvicorn app.main:app --host 192.168.1.50 --port 8001`

Production setup:
- Multiple workers: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4`
  - Note: each worker is a separate process. Share cross-process state using DB/Redis/etc.

## Endpoints
- `GET /health` → `{ "status": "ok" }`
- `POST /chat` with JSON:
  - Request: `{ "message": "Your question here" }`
  - Response: `{ "reply": "Agent response with RAG context" }`

Example:
```bash
curl -X POST "http://127.0.0.1:8001/chat" \
  -H "Content-Type: application/json" \
  -d '{ "message": "What is in the BioMed IoT concept?" }'
```

## Important env vars
Place in `.env` or set in your environment:
- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional; defaults to `gpt-4.1`)
- `MCP_FS_ROOTS` (optional; file system roots for MCP)
- `SQLITE_DB_PATH` (optional; e.g., `data/demo.db`)

## Project layout
- `app/` — FastAPI app and agent wiring
  - `main.py` — FastAPI entry point with `/health` and `/chat` endpoints
  - `agent_cli_mcp.py` — exposes `build_agent()` and MCP configuration
  - `client.py`, `client_direct.py` - client implementations
- `rag/` - Retrieval-Augmented Generation setup
  - `create_vector_store.py` — utilities to create and manage OpenAI Vector Stores
  - `update_file.py` — upload documents to OpenAI Files API
  - Sample PDF: `BioT_IoT_AppKonzept_c2q3.pdf`
- `mcp_server/` — example MCP integrations (e.g., `dice_and_sport.py`)
- `data/` — sample SQLite DB (`data/demo.db`)
- `sample_files/` — supporting files

## RAG Setup
Check https://platform.openai.com/docs/guides/tools-file-search for details like creating vector stores by uploading files.

## Troubleshooting
- `ModuleNotFoundError: No module named 'app.main'`
  - Run from project root and ensure `app/__init__.py` exists.
- Port in use:
  - Change `--port` or stop the conflicting process.
- Missing OPENAI_API_KEY:
  - Create `.env` file with `OPENAI_API_KEY=sk-...`
- Agent/MCP errors:
  - Verify env vars and that the MCP service is reachable.
  - If your MCP runs on `http://localhost:8000/mcp`, run this server on a different port (e.g., `8001`).
- Vector Store issues:
  - Ensure file is uploaded and processing is complete.
  - Check vector store ID is valid via OpenAI dashboard.

## Windows firewall / Linux UFW
- Windows: allow the chosen port or the uvicorn process when prompted.
- Ubuntu UFW: `sudo ufw allow 8001`

## Notes
- When running multiple workers, persist shared state externally (DB, Redis).
- The server intentionally reuses CLI agent code so behavior matches your CLI runs.
