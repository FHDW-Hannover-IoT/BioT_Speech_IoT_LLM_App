# BioT Sensor Assistant (LLM_App)

The Python backend for the BioT Speech IoT project. Hosts:

1. A **FastAPI server** that exposes `POST /chat` for the Android app.
2. An **LLM agent** (provider-agnostic — Anthropic, OpenAI, DeepSeek, Gemini).
3. A **proper MCP server** (`FastMCP`, Streamable HTTP, port 8002) that exposes
   sensor query tools.
4. An **MQTT subscriber** that mirrors live sensor readings into a SQLite
   database the agent can query.

> **System role:** This is the server side of the BioT split.
> The Android app is the client side — it owns the on-phone Room database
> for live charts. The server's SQLite is a separate, parallel view of the
> same MQTT data, used only by the LLM. See [Database architecture](#database-architecture).

---

## Table of Contents

1. [Architecture](#architecture)
2. [Database architecture](#database-architecture)
3. [Prerequisites](#prerequisites)
4. [Quick start](#quick-start)
5. [Endpoints](#endpoints)
6. [Project layout](#project-layout)
7. [Environment variables](#environment-variables)
8. [Switching LLM provider](#switching-llm-provider)
9. [Adding a new MCP tool](#adding-a-new-mcp-tool)
10. [Troubleshooting](#troubleshooting)

---

## Architecture

```
                                ┌──────────────────────────────┐
                                │  Android app (BioT_Speech_IoT_App)
                                │  - Voice → VoiceCommandResolver
                                │  - QUERY_* + UNKNOWN → LlmQueryHandler
                                │      │
                                └──────┼───────────────────────┘
                                       │ POST /chat  { "message": "..." }
                                       ▼
                  ┌────────────────────────────────────────┐
                  │  app/main.py  (FastAPI, port 8001)     │
                  │   ├── /health                           │
                  │   └── /chat ─► SensorAgent.run()        │
                  │                  │                      │
                  │                  ▼                      │
                  │            app/agent.py                 │
                  │            (provider-agnostic)          │
                  └──────┬─────────────────────┬────────────┘
                         │ HTTP                │ tool calls
                         ▼                     ▼
       ┌───────────────────────────┐   ┌───────────────────────────┐
       │ Anthropic / OpenAI /      │   │ mcp_server/                │
       │ DeepSeek / Gemini API     │   │ sensor_mcp_server.py       │
       │ (chosen by LLM_PROVIDER)  │   │ FastMCP, port 8002         │
       └───────────────────────────┘   └────────────┬───────────────┘
                                                    │ SELECT
                                                    ▼
                                       ┌────────────────────────────┐
                                       │ data/sensor_database.db    │
                                       │  (SQLite, read-only by MCP)│
                                       └────────────▲───────────────┘
                                                    │ INSERT
                              ┌─────────────────────┴─────────────────┐
                              │ mcp_server/mqtt_subscriber.py         │
                              │ (background thread inside FastAPI)    │
                              └─────────────────▲─────────────────────┘
                                                │ subscribes Sensor/*
                                                │
                              ┌─────────────────┴─────────────────────┐
                              │ Mosquitto MQTT broker                 │
                              └─────────────────▲─────────────────────┘
                                                │ publishes Sensor/*
                              ┌─────────────────┴─────────────────────┐
                              │ ESP8266 + MPU-6050 + A3144            │
                              └───────────────────────────────────────┘
```

The MCP server runs as a **subprocess** of the FastAPI server — `app/main.py`
spawns it during the FastAPI lifespan startup. The agent reaches it over HTTP
on `localhost:8002`. To deploy publicly, change `MCP_SERVER_URL` in `.env`
without changing any code.

---

## Database architecture

The BioT system has **two SQLite databases on purpose**. This is intentional and
documented here so it doesn't look like a bug.

| Database | Location | Written by | Read by | Purpose |
|---|---|---|---|---|
| **Room (on phone)** | `/data/data/com.fhdw.biot.speech.iot/databases/sensor_database` | Android `MainActivity` from MQTT messages | All chart Activities via `LiveData` | Reactive UI, offline charts, date filters |
| **Server SQLite** | `LLM_App/data/sensor_database.db` | `mqtt_subscriber.py` from MQTT messages | MCP tools (read-only) | LLM historical queries, anomaly analysis |

Both databases use **identical schemas** (see `mqtt_subscriber._ensure_db_schema`)
so the agent's SQL queries work the same way against either one. They both subscribe
to the same MQTT topics — they just write to different files.

**Why not one DB?**
- Room lives inside the Android app sandbox. The MCP server can't reach it
  without `adb pull`.
- Stripping Room out of the Android app would mean rewriting every chart
  Activity (`AccelActivity`, `GyroActivity`, `MagnetActivity`,
  `MainGraphActivity`) to fetch from HTTP endpoints and would break offline charts.
- The "extra" cost of two DBs is one background thread (`mqtt_subscriber`) that
  was running anyway. The disk overhead is negligible compared to the MQTT
  message volume the system handles.

---

## Prerequisites

- Python 3.10+ — https://www.python.org/downloads/
- `uv` (Astral's package manager). Install via PowerShell:
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
- Mosquitto MQTT broker running on the same machine (or reachable on the LAN)
- An API key from one of: Anthropic / OpenAI / DeepSeek / Gemini

---

## Quick start

```powershell
# 1. Clone and enter the project
cd C:\Users\Arun\Documents\School\BioT_Speech_IoT_LLM_App

# 2. Create a virtualenv and install dependencies
uv venv
.venv\Scripts\Activate.ps1
uv sync

# 3. Configure
copy .env.example .env
# … edit .env and set LLM_API_KEY

# 4. Make sure Mosquitto is running
net start mosquitto

# 5. Start the FastAPI server (which launches the MCP server + MQTT subscriber)
uv run python -m app.main
```

You should see log lines like:

```
INFO  | biot.app.main | Starting MCP sensor server on port 8002
INFO  | biot.app.main | MCP server ready at http://localhost:8002/mcp
INFO  | biot.app.main | Agent ready (provider=anthropic, model=claude-sonnet-4-6, mcp=http://localhost:8002/mcp)
INFO  | biot.app.main | MQTT subscriber running (broker=127.0.0.1:1883)
INFO  | uvicorn.error | Application startup complete.
```

Quick smoke test:

```powershell
curl.exe -X POST http://127.0.0.1:8001/chat `
  -H "Content-Type: application/json" `
  -d '{ "message": "How many gyro readings are in the database?" }'
```

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | Liveness check — returns `{ "status": "ok" }` |
| `POST` | `/chat`   | Send a natural-language query, get a structured JSON action back |

### `/chat` request

```json
{ "message": "What is the latest gyro Y value?" }
```

### `/chat` response

The agent always returns one of these action types (see
[`docs/LLM_USE_CASES.md`](docs/LLM_USE_CASES.md) for the full schema):

```json
{
  "reply": "{ \"action\": \"answer\", \"tts\": \"Gyro axis Y is 0.45 degrees per second, 2 seconds ago.\" }"
}
```

The Android app's `LlmQueryHandler` parses the inner JSON and dispatches the action.

---

## Project layout

```
LLM_App/
├── app/
│   ├── main.py              # FastAPI entry point + lifespan that starts MCP + MQTT
│   ├── agent.py             # SensorAgent — provider-agnostic LLM orchestration
│   ├── database.py          # Direct SQLite access helpers (used outside MCP)
│   ├── logger.py            # Centralised rotating-file + console logger
│   └── providers/
│       ├── base.py          # Abstract LLMProvider interface
│       ├── anthropic.py     # Claude implementation
│       ├── openai.py        # GPT implementation
│       ├── deepseek.py      # DeepSeek implementation
│       ├── gemini.py        # Gemini implementation
│       └── __init__.py      # create_provider() factory
├── mcp_server/
│   ├── sensor_mcp_server.py # FastMCP server exposing 7 sensor tools
│   └── mqtt_subscriber.py   # Background MQTT → SQLite writer
├── config/
│   └── settings.py          # Settings class — reads from .env
├── docs/
│   └── LLM_USE_CASES.md     # Source of truth for what the LLM must handle
├── data/
│   └── sensor_database.db   # Server-side SQLite (created on first MQTT message)
├── logs/
│   └── biot.log             # Rotating log file (5 MB × 3)
├── .env                     # Real credentials (gitignored)
├── .env.example             # Safe template (committed)
├── pyproject.toml
└── README.md                # ← you are here
```

---

## Environment variables

See [`.env.example`](.env.example) for the canonical list. Key ones:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | yes | `anthropic` | One of: anthropic, openai, deepseek, gemini |
| `LLM_API_KEY`  | yes | —          | Key for the chosen provider |
| `LLM_MODEL`    | no  | per-provider default | e.g. `claude-sonnet-4-6` |
| `SQLITE_DB_PATH` | no | `data/sensor_database.db` | Server-side DB path |
| `SERVER_PORT`  | no  | `8001`     | FastAPI port |
| `MCP_SERVER_PORT` | no | `8002`    | MCP subprocess port |
| `MQTT_BROKER_HOST` | no | `127.0.0.1` | Mosquitto host |
| `MQTT_BROKER_PORT` | no | `1883`    | Mosquitto port |

---

## Switching LLM provider

Change one line in `.env`:

```env
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
```

No code changes needed. The factory in `app/providers/__init__.py` resolves the
correct implementation at startup. Each provider implements the same `LLMProvider`
abstract class, so the agent code never knows which one is in use.

To add a new provider (e.g. Mistral):
1. Create `app/providers/mistral.py` subclassing `LLMProvider`.
2. Add `mistral` to `_SUPPORTED` in `app/providers/__init__.py`.
3. Add the import + return branch in `create_provider()`.
4. Add the default model in `config/settings.py`.

---

## Adding a new MCP tool

1. Add a `@mcp.tool()` function in `mcp_server/sensor_mcp_server.py`.
2. Add a corresponding `_TOOLS` entry in `app/agent.py` so the LLM knows it
   exists.
3. Restart the FastAPI server — the MCP subprocess restarts with it.

The tool gets called from `SensorAgent._dispatch_tool` over HTTP, so there's no
SDK setup or registration boilerplate beyond those two files.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `MCP server unreachable at http://localhost:8002/mcp` in agent logs | The subprocess crashed during startup | Check the terminal output above for the Python traceback — usually a missing module or a port collision |
| `Database file does not exist` from a tool call | The MQTT subscriber hasn't received any messages yet | Confirm Mosquitto is running, the ESP8266 is publishing, and `MQTT_BROKER_HOST` matches the broker's IP |
| FastAPI starts but `/chat` always returns "agent not ready" | `LLM_API_KEY` is empty or invalid | Set the key in `.env` and restart |
| Logs show the agent calling `execute_query` repeatedly with weird SQL | The model is hallucinating column names | Run the query manually with `sqlite3` to see the schema, then improve the system prompt to mention the columns explicitly |
| Port 8001 in use | Another process bound to the FastAPI port | Change `SERVER_PORT` in `.env` |

---

*BioT Speech IoT — LLM_App | FHDW Hannover IoT 2025-26*
