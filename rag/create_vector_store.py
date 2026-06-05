import os
import time
from pathlib import Path
from typing import Iterable, Sequence

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


def _normalize_file_ids(file_ids: Sequence[str] | str | None) -> list[str]:
    if not file_ids:
        return []
    if isinstance(file_ids, str):
        return [file_ids]
    return list(file_ids)


def _wait_for_processing(
    client: OpenAI,
    vector_store_id: str,
    timeout_secs: int = 120,
    poll_interval_secs: float = 2.0,
) -> None:
    start = time.time()
    while time.time() - start < timeout_secs:
        result = client.vector_stores.files.list(vector_store_id=vector_store_id)
        items = getattr(result, "data", None)
        if not items:
            return

        statuses = []
        for item in items:
            status = getattr(item, "status", None)
            if isinstance(item, dict):
                status = item.get("status")
            if status:
                statuses.append(status)

        if not statuses:
            return
        if all(status in {"completed", "processed", "ready"} for status in statuses):
            return

        time.sleep(poll_interval_secs)


def add_files_to_vector_store(
    vector_store_id: str,
    file_ids: Iterable[str],
    wait: bool = True,
) -> list[str]:
    client = _get_client()
    attached: list[str] = []
    for file_id in file_ids:
        result = client.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=file_id,
        )
        attached.append(result.id)

    if wait:
        _wait_for_processing(client, vector_store_id)

    return attached


def create_vector_store(
    store_name: str,
    file_ids: Sequence[str] | str | None = None,
    wait: bool = True,
    manifest_path: str | None = None,
) -> str:
    client = _get_client()
    vector_store = client.vector_stores.create(name=store_name)
    normalized_file_ids = _normalize_file_ids(file_ids)
    if normalized_file_ids:
        add_files_to_vector_store(vector_store.id, normalized_file_ids, wait=wait)
    if manifest_path:
        update_manifest(
            manifest_path,
            vector_store_id=vector_store.id,
            file_ids=normalized_file_ids,
        )
    return vector_store.id


def delete_vector_store(store_id: str) -> None:
    client = _get_client()
    result = client.vector_stores.delete(vector_store_id=store_id)
    print(result)


if __name__ == "__main__":
    manifest_path = Path(__file__).resolve().parent / "rag_manifest.json"
    store_id = create_vector_store(
        store_name="Integration Project BioMed IoT App Concept",
        file_ids="file-GKRNQ9x5FCbjF7UuE6Enrv",
        manifest_path=str(manifest_path),
    )  # -> created vector store id: vs_69160490a5b08191a4ecd657e91a65ec
    print(store_id)
