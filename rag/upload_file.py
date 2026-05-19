import os
from io import BytesIO
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
from openai import OpenAI

try:
    from rag.manifest import update_manifest
except ImportError:
    from manifest import update_manifest

load_dotenv()


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY or LLM_API_KEY. Put it in a .env file or the environment."
        )
    return OpenAI(api_key=api_key)


def _open_url(url: str) -> tuple[str, BytesIO]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    file_name = url.split("/")[-1] or "download"
    return file_name, BytesIO(response.content)


def create_file(
    file_path: str,
    purpose: str = "assistants",
    verbose: bool = True,
    manifest_path: str | None = None,
    source: str | None = None,
) -> str:
    client = _get_client()
    if file_path.startswith("http://") or file_path.startswith("https://"):
        file_name, file_content = _open_url(file_path)
        file_tuple = (file_name, file_content)
        result = client.files.create(file=file_tuple, purpose=purpose)
    else:
        with open(file_path, "rb") as file_content:
            result = client.files.create(file=file_content, purpose=purpose)
    if verbose:
        print(result.id)
    if manifest_path:
        update_manifest(
            manifest_path,
            file_ids=[result.id],
            sources=[source or file_path],
        )
    return result.id


def create_files(
    file_paths: Iterable[str],
    purpose: str = "assistants",
    verbose: bool = True,
    manifest_path: str | None = None,
) -> list[str]:
    ids: list[str] = []
    sources: list[str] = []
    for path in file_paths:
        ids.append(create_file(path, purpose=purpose, verbose=verbose))
        sources.append(path)
    if manifest_path:
        update_manifest(manifest_path, file_ids=ids, sources=sources)
    return ids


def delete_file(file_id_to_delete: str, verbose: bool = True) -> None:
    client = _get_client()
    result = client.files.delete(file_id=file_id_to_delete)
    if verbose:
        print(result)


if __name__ == "__main__":
    # Replace with your own file path or URL
    file_path = Path(__file__).resolve().parent / "Doku.pdf"
    if not file_path.exists():
        raise FileNotFoundError(f"Missing file: {file_path}")
    manifest_path = Path(__file__).resolve().parent / "rag_manifest.json"
    file_id = create_file(str(file_path), manifest_path=str(manifest_path))
    print(f"Created file with ID: {file_id}")