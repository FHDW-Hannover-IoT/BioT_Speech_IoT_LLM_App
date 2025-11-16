import json
import os
import sys
import time
from typing import Optional

try:
    # optional: load .env if available
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install with `uv add requests` (or `pip install requests`).")
    sys.exit(1)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def load_env():
    if load_dotenv:
        load_dotenv()

def get_server_url() -> str:
    return os.getenv("CHAT_SERVER_URL", "http://127.0.0.1:8001/chat").strip()

def check_health(base_url: str, timeout: float = 5.0) -> None:
    # If user passed full /chat URL, derive health path; else assume base ends with /chat
    if base_url.endswith("/chat"):
        health_url = base_url[:-5] + "/health"
    else:
        # assume they provided server base already
        health_url = base_url.rstrip("/") + "/health"

    try:
        r = requests.get(health_url, timeout=timeout)
        if r.ok:
            eprint(f"[health] {r.json()}")
        else:
            eprint(f"[health] HTTP {r.status_code}: {r.text.strip()}")
    except Exception as ex:
        eprint(f"[health] Could not reach server at {health_url}: {ex}")

def post_chat(server_url: str, message: str, timeout: float = 30.0) -> Optional[str]:
    try:
        r = requests.post(
            server_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"message": message}, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        if r.ok:
            # Expected shape: {"reply": "..."}
            try:
                data = r.json()
                return str(data.get("reply", "")).strip()
            except json.JSONDecodeError:
                return r.text.strip()
        else:
            return f"[error] HTTP {r.status_code}: {r.text.strip()}"
    except requests.exceptions.Timeout:
        return "[error] Request timed out."
    except requests.exceptions.ConnectionError as ce:
        return f"[error] Connection error: {ce}"
    except Exception as ex:
        return f"[error] {ex}"

def main():
    # Ensure stdout prints UTF-8 on Windows if possible
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # python 3.7+
    except Exception:
        pass

    load_env()
    server_url = get_server_url()

    eprint("=== Chat Client ===")
    eprint(f"Server: {server_url}")
    eprint("Type your message and press Enter. Commands: /quit, /exit, /health, /set <url>")
    eprint("Press Ctrl-C to exit.\n")

    # quick health check
    check_health(server_url)

    while True:
        try:
            line = input("You> ").strip()
        except EOFError:
            eprint("\n[bye] EOF")
            break
        except KeyboardInterrupt:
            eprint("\n[bye] Interrupted")
            break

        if not line:
            continue

        # commands
        if line.lower() in ("/quit", "/exit"):
            eprint("[bye] Exit requested")
            break
        if line.lower() == "/health":
            check_health(server_url)
            continue
        if line.lower().startswith("/set "):
            new_url = line[5:].strip()
            if not new_url:
                eprint("[set] Usage: /set http://127.0.0.1:8000/chat")
                continue
            server_url = new_url
            eprint(f"[set] Server set to: {server_url}")
            continue

        # send to server
        start = time.time()
        reply = post_chat(server_url, line)
        elapsed = time.time() - start

        if reply is None or reply == "":
            print("Assistant> [empty response]")
        else:
            print(f"Assistant> {reply}")
        eprint(f"[elapsed] {elapsed:.2f}s")

if __name__ == "__main__":
    main()