# FastAPI Agent HTTP Server with RAG

Minimal HTTP wrapper around the OpenAI Agents setup in `app/agent_cli_mcp.py` with Retrieval-Augmented Generation (RAG) support using OpenAI's Vector Stores API.

## Features
- Reuses `build_agent()` from `app/agent_cli_mcp.py` so CLI and server share the same wiring.
- RAG integration via OpenAI Vector Stores for document-based Q&A.
- Lightweight FastAPI + Uvicorn server.
- MCP (Model Context Protocol) integrations for extensibility.
- Configurable via environment variables.

## Requirements
- Python 3.10+ (project uses `pyproject.toml`) download here: https://www.python.org/downloads/
- `uv tools` run this in powershell :'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
- Install NPM using 'npm install -g npm' in powershell
- Install Docker here: https://www.docker.com/ and follow the 4 steps shown here
  -docker pull node:24-alpine
  -docker run -it --rm --entrypoint sh node:24-alpine
  -node -v # Should print "v24.11.1".
  -npm -v # Should print "11.6.2".

## Quick start (using `uv`)
0. Clone this Repository
1. Create a virtual env (optional):
   - `uv venv`
   - Activate: `. .venv/bin/activate` (Linux/macOS) or `.venv\Scripts\Activate.ps1` (Windows PowerShell)
2. run this in the standard folder in powershell or cmd: npx -y @modelcontextprotocol/server-filesystem 
3. Configure environment:
   - Create 2 new folders sample_files & data (see example below)
   - Set your `OPENAI_API_KEY`
   - Set `MCP_FS_ROOTS` example: C:\Users\name\...\BioT_Speech_IoT_LLM_App\sample_files
   - Set `SQLITE_DB_PATH` example: C:\Users\name\...\BioT_Speech_IoT_LLM_App\data\database.db
4. Open the folder "rag" and open both .py files on your local texteditor (vscode).

-In file 'upload_file.py' change the last line to the right pdf's path located in the same folder.
-(example: "C:\Users\admin\...\Desktop\Integrationsprojekt IoT\BioT_Speech_IoT_LLM_App\rag\BioT_Iot_AppKonzept_c2q3.pdf")

-Open powershell in the same folder and run following command: uv run python .\upload_file.py
-You will now recieve a new FileId as a response. Copy the FileId and replace the old FildId with the newly generated one in the 'upload_file.py' document.

-In file 'create_vector_store.py' paste the just generated FileId in line 39

-Open powershell in the same folder and run following command: uv run python .\create_vector_store.py
-You will recieve your own vector store id. Open 'agent_cli_mcp' in folder app with your texteditor and replace the vectorstoreid in line 126 with the newly generated one.

5. Run the server locally:
   - uv run python .\mcp_server\dice_and_sport.py
   - uv run python .\app\agent_cli_mcp.py   


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
