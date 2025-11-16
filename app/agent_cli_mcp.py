import os
import sys
import asyncio
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from agents import Agent, FileSearchTool, Runner
from agents.model_settings import ModelSettings
from agents.mcp import MCPServerStreamableHttp
from agents.mcp import MCPServerStdio

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL")  # e.g., "gpt-4.1"

# OpenAI model temperature is a hyperparameter that controls the randomness of the model's output,
# with a range typically from 0 to 2. A lower temperature (e.g., 0.2) makes the output more deterministic and focused,
# ideal for tasks like code generation or translation, while a higher temperature (e.g., 0.8) increases randomness
# and creativity for tasks like creative writing or brainstorming.
# The default temperature is often around 0.7, which balances coherence and diversity.
MODEL_SETTINGS = ModelSettings(temperature=0.2)

INSTRUCTIONS = """\
You are a helpful coding + data + sport + Integration Project of BioMed IoT App assistant.
- Prefer using available MCP tools (filesystem, sqlite, sport recommender) instead of inventing data.
- For files: use the filesystem tools within allowed roots.
- For SQLite: inspect schema, then write safe, parameterized queries.
- For sport recommendations: use the sport recommender tool.
- For questions regarding Integration Project of BioMed IoT App, use the provided project concept file.
- Be concise unless asked otherwise.
"""

_raw_roots = os.getenv("MCP_FS_ROOTS", "").strip()
if _raw_roots:
    FS_ROOTS = [p.strip() for p in _raw_roots.replace(";", ",").split(",") if p.strip()]
    for r in FS_ROOTS:
        Path(r).mkdir(parents=True, exist_ok=True)
else:
    samples_dir = Path(__file__).parent / "sample_files"
    samples_dir.mkdir(exist_ok=True)
    FS_ROOTS = [str(samples_dir)]  # default demo root

SQLITE_DB_PATH = Path(os.getenv("SQLITE_DB_PATH", "data/demo.db"))
SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # ensure parent dir exists

# Keep a reference so we can disconnect on exit
sport_server: Optional[MCPServerStreamableHttp] = None
filesystem_server: Optional[MCPServerStdio] = None
sqlite_server: Optional[MCPServerStdio] = None

async def build_agent():
    global sport_server, filesystem_server, sqlite_server

    if not os.getenv("OPENAI_API_KEY"):
        print("Missing OPENAI_API_KEY in environment/.env", file=sys.stderr)
        raise SystemExit(1)

    # --- Sport Recommender MCP via Streamable HTTP ---
    try:
        sport_server = MCPServerStreamableHttp(
            name="sport_recommender",
            params={
                "url": "http://localhost:8000/mcp",
                "timeout": 10,
            },
            cache_tools_list=True,
            max_retry_attempts=10,
        )
        await sport_server.connect()
        print("[mcp] sport recommender server connected successfully")
        sport_tools = await sport_server.list_tools()
        print("[mcp] sport recommender tools:", ", ".join(getattr(t, "name", str(t)) for t in sport_tools[:8]) or "(none)")
    except Exception as e:
        print(f"Error connecting to sport recommender server: {e}", file=sys.stderr)
        sport_server = None

    # --- Filesystem MCP via stdio (Node package, runs with npx) ---
    fs_args: List[str] = ["-y", "@modelcontextprotocol/server-filesystem", *FS_ROOTS]
    try:
        filesystem_server = MCPServerStdio(
            name="filesystem",
            params={
                "command": "npx",
                "args": fs_args
            },
        )
        await filesystem_server.connect()
        print("[mcp] filesystem server connected successfully")
        fs_tools = await filesystem_server.list_tools()
        print("[mcp] filesystem tools:", ", ".join(getattr(t, "name", str(t)) for t in fs_tools[:8]) or "(none)")
    except Exception as e:
        print(f"Error connecting to filesystem server: {e}", file=sys.stderr)
        filesystem_server = None

    # --- SQLite MCP via stdio ---
    if not SQLITE_DB_PATH:
        print("Warning: SQLITE_DB_PATH is empty; skipping SQLite MCP.", file=sys.stderr)
    else:
        try:
            sqlite_server = MCPServerStdio(
                name="sqlite",
                params={
                    "command": "npx",
                    "args": ["-y", "mcp-server-sqlite-npx", str(SQLITE_DB_PATH)],
                },
            )
            await sqlite_server.connect()
            print("[mcp] SQLite server connected successfully")
            sql_tools = await sqlite_server.list_tools()
            print("[mcp] SQLite tools:", ", ".join(getattr(t, "name", str(t)) for t in sql_tools[:8]) or "(none)")
        except Exception as e:
            print(f"Error connecting to SQLite server: {e}", file=sys.stderr)
            sqlite_server = None

    # Build the agent with both servers (sqlite may be None if skipped)
    mcp_servers = ([sport_server] if sport_server else []) + ([filesystem_server] if filesystem_server else []) + ([sqlite_server] if sqlite_server else [])
    agent_kwargs = {
        "name": "Dev Copilot",
        "instructions": INSTRUCTIONS,
        "model_settings": MODEL_SETTINGS,
        "mcp_servers": mcp_servers,
        "tools": [
            FileSearchTool(
                max_num_results=3,
                vector_store_ids=["vs_69160490a5b08191a4ecd657e91a65ec"],
            )
        ],
    }
    if MODEL:
        agent_kwargs["model"] = MODEL

    return Agent(**agent_kwargs)

async def main():
    agent = None
    try:
        agent = await build_agent()
        if not agent:
            print("Failed to initialize agent", file=sys.stderr)
            return

        print("=== Agent SDK + MCP (FS + SQLite) ===")
        print("Persona:", agent.name)
        print("Tools: filesystem via MCP (root:", FS_ROOTS, ")")
        print("Tools: SQLite via MCP (db file path:", SQLITE_DB_PATH, ")")
        print("Commands: /quit, /exit")
        print()

        while True:
            try:
                user = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[bye]")
                break

            if not user:
                continue
            if user.lower() in ("/quit", "/exit"):
                print("[bye]")
                break

            try:
                result = await Runner.run(agent, user)
                print("Assistant>", result.final_output or "[no output]")
            except Exception as e:
                print(f"Error running agent: {e}", file=sys.stderr)

    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
    finally:
        if agent:
            for server in (sport_server, sqlite_server, filesystem_server):
                if server is not None:
                    try:
                        await asyncio.wait_for(server.cleanup(), timeout=5)
                    except Exception as e:
                        print(f"Error during cleanup: {e}", file=sys.stderr)
            print("[mcp] disconnected")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")