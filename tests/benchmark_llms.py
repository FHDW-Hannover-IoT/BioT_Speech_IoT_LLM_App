import json
import os
import sys
import time
from pathlib import Path

# Fix ModuleNotFoundError when running script directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.agent import SensorAgent
from config.settings import settings

QUESTIONS = [
    "How do I view sensor data?",
    "How can I define events?",
    "What types of events are there?",
    "What is today's date?",
    "How do I turn off the system?",
    "How do I display a specific axis on the screen?",
    "How do I select a specific axis from the gyro sensor?",
    "How can I view the events?",
    "What types of events are there?",
    "How do I start calibration?",
    "What does “Get Mode” mean?",
]

MODELS_CONFIG = [
     {
         "provider": "openai",
         "model": "gpt-5.4-mini",
         "folder_name": "LLM1_ChatGPT_5.4_Mini",
         "env_key": "OPENAI_API_KEY",
     },
    {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "folder_name": "LLM2_Claude_Haiku_4.5",
        "env_key": "ANTHROPIC_API_KEY",
    }
]


def call_with_retry(func, *args, max_retries=5, **kwargs):
    """Call a function and automatically retry if we hit Gemini/OpenAI rate limits."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if attempt < max_retries - 1:
                    wait_time = 15 * (attempt + 1)
                    print(f"      [!] Rate limit hit (429). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
            # If it's not a rate limit error, or we ran out of retries, raise it
            raise

def main():
    load_dotenv()

    # Base data directory
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print("Starting LLM Benchmarking (Raw vs Processed)...")

    from app.providers import create_provider
    from rag.advanced_rag import AdvancedRag
    from rag.manifest import read_manifest

    # Get the Vector Store ID for RAG
    manifest_path = settings.rag_manifest_path
    store_id = None
    if manifest_path.exists():
        manifest = read_manifest(str(manifest_path))
        store_id = manifest.get("vector_store_id")
    
    if not store_id:
        print("Warning: No vector_store_id found in manifest. Processed RAG queries will have no context.")

    for config in MODELS_CONFIG:
        provider_name = config["provider"]
        model = config["model"]
        folder_name = config["folder_name"]
        env_key = config["env_key"]

        # Determine API key for the provider from .env
        api_key = os.getenv(env_key) or os.getenv("LLM_API_KEY", "")
        if not api_key:
            print(
                f"\nSkipping {provider_name} ({model}) - no API key found for {env_key} or LLM_API_KEY."
            )
            continue

        print(f"\n--- Testing Provider: {provider_name} | Model: {model} ---")

        # Initialize raw Provider and Advanced RAG
        try:
            # We pass a dummy tool_dispatcher since we don't use tools for the raw benchmarking
            provider = create_provider(
                provider_name=provider_name,
                api_key=api_key,
                model=model,
                tool_dispatcher=lambda n, a: ""
            )
            rag = AdvancedRag(llm_provider=provider)
        except Exception as e:
            print(f"Failed to initialize provider for {provider_name}: {e}")
            continue

        # Define target directories (using default existing ones)
        base_model_dir = data_dir / folder_name
        input_raw_dir = base_model_dir / "input" / "raw-data"
        input_proc_dir = base_model_dir / "input" / "processed-data"
        output_dir = base_model_dir / "rag-data" / "output"
        timings_csv_path = base_model_dir / "rag-data" / "timings.csv"

        # Ensure directories exist just in case, though user said use default
        input_raw_dir.mkdir(parents=True, exist_ok=True)
        input_proc_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize or clear timings.csv
        with open(timings_csv_path, "w", encoding="utf-8") as f_csv:
            f_csv.write("Question-Id,Raw-Time-ms,Processed-Time-ms\n")

        for i, question in enumerate(QUESTIONS, start=1):
            print(f"  [{i}/{len(QUESTIONS)}] Question: {question}")
            
            q_id = f"q{i:02d}"

            # ---------------------------------------------------------
            # 1. RAW DATA (Pure Query)
            # ---------------------------------------------------------
            start_time = time.time()
            try:
                raw_system_prompt = "Answer the question directly and concisely."
                raw_response = call_with_retry(
                    provider.run,
                    user_message=question,
                    tools=[],
                    system_prompt=raw_system_prompt
                )
            except Exception as e:
                raw_response = str(e)
            elapsed_raw_ms = int((time.time() - start_time) * 1000)

            # Write raw input
            with open(input_raw_dir / f"{q_id}.txt", "w", encoding="utf-8") as f:
                f.write(question)
                
            # Write raw output
            with open(output_dir / f"{q_id}_raw.txt", "w", encoding="utf-8") as f:
                f.write(raw_response)

            # ---------------------------------------------------------
            # 2. PROCESSED DATA (Advanced RAG Query)
            # ---------------------------------------------------------
            start_time = time.time()
            try:
                if store_id:
                    rag_ans = call_with_retry(rag.answer, question, vector_store_id=store_id)
                    proc_response = rag_ans.answer
                    augmented_input = rag_ans.augmented_prompt
                else:
                    proc_response = "No vector store available."
                    augmented_input = question
            except Exception as e:
                proc_response = str(e)
                augmented_input = question
            elapsed_proc_ms = int((time.time() - start_time) * 1000)

            # Write processed input
            with open(input_proc_dir / f"{q_id}.txt", "w", encoding="utf-8") as f:
                f.write(augmented_input)

            # Write processed output
            with open(output_dir / f"{q_id}_processed.txt", "w", encoding="utf-8") as f:
                f.write(proc_response)

            # Append to timings CSV
            with open(timings_csv_path, "a", encoding="utf-8") as f_csv:
                f_csv.write(f"{q_id},{elapsed_raw_ms},{elapsed_proc_ms}\n")

            print(f"    -> Raw: {elapsed_raw_ms}ms | Processed: {elapsed_proc_ms}ms")
            
            # Prevent hitting Rate Limits
            if i < len(QUESTIONS):
                print("    -> Sleeping 5s between questions...")
                time.sleep(5)

    print("\nBenchmarking complete!")


if __name__ == "__main__":
    main()
