import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from rag.create_vector_store import add_files_to_vector_store, create_vector_store
from rag.manifest import read_manifest, update_manifest
from rag.upload_file import create_files

load_dotenv()

_DEFAULT_MODEL = os.getenv("OPENAI_RAG_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
_DEFAULT_RERANK_MODEL = os.getenv("OPENAI_RERANK_MODEL", _DEFAULT_MODEL)


@dataclass(frozen=True)
class SearchResult:
    text: str
    score: float | None
    file_id: str | None
    filename: str | None


@dataclass(frozen=True)
class RagAnswer:
    answer: str
    sources: list[str]


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY or LLM_API_KEY. Put it in a .env file or the environment."
        )
    return OpenAI(api_key=api_key)


def _safe_json_loads(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return {}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                texts.append(str(part.get("text", "")))
            elif hasattr(part, "text"):
                texts.append(str(getattr(part, "text", "")))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t)
    if isinstance(content, dict) and "text" in content:
        return str(content.get("text", ""))
    return ""


class AdvancedRag:
    def __init__(
        self,
        client: OpenAI | None = None,
        model: str | None = None,
        rerank_model: str | None = None,
        use_llm_rerank: bool = True,
    ) -> None:
        self._client = client or _get_client()
        self._model = model or _DEFAULT_MODEL
        self._rerank_model = rerank_model or _DEFAULT_RERANK_MODEL
        self._use_llm_rerank = use_llm_rerank

    def generate_queries(self, question: str, max_queries: int = 4) -> list[str]:
        system = (
            "You generate search queries for retrieval. "
            "Return JSON: {\"queries\": [\"...\"]}."
        )
        user = (
            "Question:\n"
            f"{question}\n\n"
            f"Rules:\n- Return {max_queries} to {max_queries + 1} short queries.\n"
            "- No punctuation other than quotes.\n- Keep each query under 12 words."
        )
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
        data = _safe_json_loads(text)
        queries = data.get("queries") if isinstance(data.get("queries"), list) else []
        cleaned = []
        for q in queries:
            if isinstance(q, str) and q.strip():
                cleaned.append(q.strip())
        if question not in cleaned:
            cleaned.append(question)
        return cleaned

    def _vector_store_search(self, vector_store_id: str, query: str, top_k: int) -> Any:
        search_fn = getattr(self._client.vector_stores, "search", None)
        if not search_fn:
            raise RuntimeError(
                "OpenAI client does not support vector store search. "
                "Update the openai package or use the Responses API with file_search."
            )
        for kwargs in (
            {"max_results": top_k},
            {"top_k": top_k},
            {"limit": top_k},
        ):
            try:
                return search_fn(vector_store_id=vector_store_id, query=query, **kwargs)
            except TypeError:
                continue
        return search_fn(vector_store_id=vector_store_id, query=query)

    def _parse_search_results(self, response: Any) -> list[SearchResult]:
        payload = _to_dict(response)
        data = payload.get("data") or getattr(response, "data", None) or []
        if not isinstance(data, list):
            return []

        results: list[SearchResult] = []
        for item in data:
            item_dict = _to_dict(item)
            content = item_dict.get("content") or getattr(item, "content", None)
            text = _content_to_text(content) or _content_to_text(item_dict)
            if not text:
                continue

            score = item_dict.get("score") or item_dict.get("relevance_score")
            file_id = item_dict.get("file_id")
            filename = item_dict.get("filename")
            file_obj = item_dict.get("file") or {}
            if isinstance(file_obj, dict):
                file_id = file_id or file_obj.get("id")
                filename = filename or file_obj.get("filename")

            results.append(
                SearchResult(
                    text=text,
                    score=score if isinstance(score, (int, float)) else None,
                    file_id=str(file_id) if file_id else None,
                    filename=str(filename) if filename else None,
                )
            )
        return results

    def retrieve(
        self,
        question: str,
        vector_store_id: str,
        top_k: int = 6,
        max_candidates: int = 24,
    ) -> list[SearchResult]:
        queries = self.generate_queries(question)
        candidates: list[SearchResult] = []
        for query in queries:
            response = self._vector_store_search(vector_store_id, query, top_k)
            candidates.extend(self._parse_search_results(response))

        deduped: dict[tuple[str | None, str], SearchResult] = {}
        for item in candidates:
            key = (item.file_id, item.text[:200])
            if key not in deduped:
                deduped[key] = item

        merged = list(deduped.values())
        merged.sort(key=lambda r: r.score or 0.0, reverse=True)
        merged = merged[:max_candidates]

        if self._use_llm_rerank and len(merged) > top_k:
            return self.rerank(question, merged, top_k=top_k)
        return merged[:top_k]

    def rerank(
        self,
        question: str,
        candidates: list[SearchResult],
        top_k: int = 6,
    ) -> list[SearchResult]:
        subset = candidates[: min(len(candidates), 20)]
        lines = []
        for idx, item in enumerate(subset, start=1):
            source = item.filename or item.file_id or f"doc-{idx}"
            lines.append(f"{idx}. Source: {source}\n{item.text}")
        prompt = (
            "Rank the passages by relevance to the question. "
            "Return JSON: {\"ranked_ids\": [1,2,3]}."
        )
        user = f"Question:\n{question}\n\nPassages:\n" + "\n\n".join(lines)

        response = self._client.chat.completions.create(
            model=self._rerank_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        text = response.choices[0].message.content or ""
        data = _safe_json_loads(text)
        ranked_ids = data.get("ranked_ids") if isinstance(data.get("ranked_ids"), list) else []

        ordered: list[SearchResult] = []
        for rid in ranked_ids:
            if isinstance(rid, int) and 1 <= rid <= len(subset):
                ordered.append(subset[rid - 1])

        if not ordered:
            ordered = subset

        return ordered[:top_k]

    def answer(
        self,
        question: str,
        vector_store_id: str,
        top_k: int = 6,
        max_context_chars: int = 8000,
    ) -> RagAnswer:
        results = self.retrieve(question, vector_store_id, top_k=top_k)
        if not results:
            return RagAnswer(
                answer="I could not find relevant context in the vector store.",
                sources=[],
            )

        context_blocks: list[str] = []
        used = 0
        sources: list[str] = []
        for idx, item in enumerate(results, start=1):
            source = item.filename or item.file_id or f"doc-{idx}"
            block = f"Source: {source}\n{item.text}".strip()
            if used + len(block) > max_context_chars:
                break
            context_blocks.append(block)
            used += len(block)
            sources.append(source)

        system = (
            "Answer the question using only the provided context. "
            "Cite sources in brackets like [source]. "
            "If the answer is not in the context, say you do not know."
        )
        user = f"Question:\n{question}\n\nContext:\n" + "\n\n".join(context_blocks)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
        return RagAnswer(answer=text.strip(), sources=sorted(set(sources)))


def _cmd_ingest(args: argparse.Namespace) -> None:
    file_ids = create_files(args.sources)
    if args.vector_store_id:
        add_files_to_vector_store(args.vector_store_id, file_ids, wait=not args.no_wait)
        store_id = args.vector_store_id
    else:
        store_id = create_vector_store(
            store_name=args.store_name,
            file_ids=file_ids,
            wait=not args.no_wait,
        )
    if args.manifest:
        update_manifest(
            args.manifest,
            vector_store_id=store_id,
            file_ids=file_ids,
            sources=list(args.sources),
        )
    print(store_id)


def _cmd_query(args: argparse.Namespace) -> None:
    store_id = args.vector_store_id
    if args.manifest:
        manifest = read_manifest(args.manifest)
        store_id = manifest.get("vector_store_id") or store_id

    if not store_id:
        raise RuntimeError("Vector store id is required for query.")

    rag = AdvancedRag(use_llm_rerank=not args.no_rerank)
    answer = rag.answer(args.question, store_id, top_k=args.top_k)
    output = {"answer": answer.answer, "sources": answer.sources}
    print(json.dumps(output, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advanced RAG utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Upload files and create or update a vector store")
    ingest.add_argument("--store-name", help="New vector store name")
    ingest.add_argument("--vector-store-id", help="Existing vector store id")
    ingest.add_argument("--sources", nargs="+", required=True, help="File paths or URLs")
    ingest.add_argument("--manifest", help="Write manifest JSON file")
    ingest.add_argument("--no-wait", action="store_true", help="Do not wait for processing")
    ingest.set_defaults(func=_cmd_ingest)

    query = sub.add_parser("query", help="Query a vector store")
    query.add_argument("--vector-store-id", help="Vector store id")
    query.add_argument("--manifest", help="Read vector store id from manifest JSON")
    query.add_argument("--question", required=True, help="User question")
    query.add_argument("--top-k", type=int, default=6, help="Top K contexts to use")
    query.add_argument("--no-rerank", action="store_true", help="Skip LLM reranking")
    query.set_defaults(func=_cmd_query)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "ingest" and not args.vector_store_id and not args.store_name:
        parser.error("ingest requires --store-name or --vector-store-id")
    args.func(args)


if __name__ == "__main__":
    main()
