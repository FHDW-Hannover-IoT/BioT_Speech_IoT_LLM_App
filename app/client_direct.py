import os
import sys
import json
import time

try:
    from dotenv import load_dotenv  # pip install python-dotenv
except Exception:
    load_dotenv = None

try:
    from openai import OpenAI  # pip install openai>=1.51
except Exception as exc:
    print("Missing dependency: openai. Install with `uv add openai` (or `pip install openai`).", file=sys.stderr)
    raise

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def load_env():
    if load_dotenv:
        load_dotenv()

def get_env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if v else default

def make_client():
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")  # optional (Azure/custom)
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in a .env file or the environment.")
    # The new SDK accepts base_url=... if you use Azure/custom endpoints.
    return OpenAI(api_key=api_key, base_url=base_url)

def create_reply(client: OpenAI, model: str, message: str) -> str:
    """
    Calls the Responses API with a simple text input.
    Returns the best-effort text reply or raises a RuntimeError.
    """
    resp = client.responses.create(model=model, input=message)

    # Preferred helper:
    text = getattr(resp, "output_text", "") or ""
    if text:
        return text.strip()

    # Robust fallback across SDK versions:
    output = getattr(resp, "output", None)
    if isinstance(output, list):
        for block in output:
            if getattr(block, "type", None) == "message":
                for content in getattr(block, "content", []) or []:
                    if getattr(content, "type", "") == "output_text" and getattr(content, "text", ""):
                        return content.text.strip()

    # Last resort: JSON dump (rare)
    try:
        return json.dumps(resp.model_dump(), ensure_ascii=False)
    except Exception:
        pass
    raise RuntimeError("Empty response from model.")

def main():
    # Ensure UTF-8 prints nicely on Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    load_env()
    model = get_env("OPENAI_MODEL", "gpt-4o-mini")

    eprint("=== ChatGPT Direct Client ===")
    eprint(f"Model: {model}")
    if os.getenv("OPENAI_BASE_URL"):
        eprint(f"Base URL: {os.getenv('OPENAI_BASE_URL')}")
    eprint("Type your message and press Enter. Commands: /quit, /exit, /model <name>")
    eprint("Press Ctrl-C to exit.\n")

    try:
        client = make_client()
    except Exception as ex:
        eprint(f"[init] {ex}")
        sys.exit(1)

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

        # Commands
        lower = line.lower()
        if lower in ("/quit", "/exit"):
            eprint("[bye] Exit requested")
            break
        if lower.startswith("/model "):
            new_model = line.split(" ", 1)[1].strip()
            if new_model:
                model = new_model
                eprint(f"[model] Using model: {model}")
            else:
                eprint("[model] Usage: /model <model-name>")
            continue

        # Call API
        start = time.time()
        try:
            reply = create_reply(client, model, line)
            print(f"Assistant> {reply}")
        except Exception as ex:
            s = str(ex)
            if "insufficient_quota" in s or "You exceeded your current quota" in s:
                eprint("[error] Insufficient quota/billing for this key. Check your plan or try another key/model.")
            else:
                eprint(f"[error] {s}")
        finally:
            eprint(f"[elapsed] {time.time() - start:.2f}s")

if __name__ == "__main__":
    main()