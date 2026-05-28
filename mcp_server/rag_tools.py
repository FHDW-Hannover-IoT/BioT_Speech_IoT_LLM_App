import os
from pathlib import Path

from app.logger import get_logger
from config.settings import settings

log = get_logger(__name__)


def _get_rag_manifest_path() -> Path:
    path_value = getattr(settings, "rag_manifest_path", None)
    if isinstance(path_value, Path):
        return path_value
    if path_value:
        return Path(str(path_value))
    return Path(__file__).resolve().parent.parent / "rag" / "rag_manifest.json"


def query_rag(question: str, top_k: int = 6) -> str:
    """
    Query the RAG vector store built from project documents.

    This helper stays importable without the MCP server dependency so tests can
    call it directly on Windows.
    """
    log.info("RAG query(question=%r, top_k=%d)", question, top_k)
    try:
        has_openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
        has_llm_key = bool(os.getenv("LLM_API_KEY", "").strip())
        if not (has_openai_key or has_llm_key):
            return "RAG requires an OpenAI API key. Set OPENAI_API_KEY or LLM_API_KEY."

        manifest_path = _get_rag_manifest_path()
        if not manifest_path.exists():
            return (
                f"RAG manifest not found at {manifest_path}. "
                "Create it with rag.advanced_rag ingest."
            )

        from rag.advanced_rag import AdvancedRag
        from rag.manifest import read_manifest

        manifest = read_manifest(str(manifest_path))
        store_id = (manifest.get("vector_store_id") or "").strip()
        if not store_id:
            return "RAG manifest is missing vector_store_id."

        top_k = max(1, min(int(top_k), 20))
        rag = AdvancedRag()
        answer = rag.answer(question, store_id, top_k=top_k)
        if answer.sources:
            return f"{answer.answer}\n\nSources: {', '.join(answer.sources)}"
        return answer.answer

    except Exception as exc:
        log.error("RAG query error: %s", exc, exc_info=True)
        return f"RAG error: {exc}"
