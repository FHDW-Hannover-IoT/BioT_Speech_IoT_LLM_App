import json
from pathlib import Path
from typing import Any, Iterable


def _default_manifest() -> dict[str, Any]:
    return {"vector_store_id": "", "file_ids": [], "sources": []}


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _normalize_manifest(data: dict[str, Any]) -> dict[str, Any]:
    normalized = _default_manifest()
    normalized["vector_store_id"] = str(data.get("vector_store_id", "") or "")

    file_ids = data.get("file_ids")
    if isinstance(file_ids, list):
        normalized["file_ids"] = _dedupe_keep_order(
            str(item) for item in file_ids if item
        )

    sources = data.get("sources")
    if isinstance(sources, list):
        normalized["sources"] = _dedupe_keep_order(
            str(item) for item in sources if item
        )

    return normalized


def read_manifest(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return _default_manifest()
    return _normalize_manifest(data)


def write_manifest(path: str, data: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_normalize_manifest(data), handle, indent=2)


def update_manifest(
    path: str,
    vector_store_id: str | None = None,
    file_ids: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
) -> dict[str, Any]:
    if Path(path).exists():
        manifest = read_manifest(path)
    else:
        manifest = _default_manifest()

    if vector_store_id:
        manifest["vector_store_id"] = vector_store_id

    if file_ids:
        manifest["file_ids"] = _dedupe_keep_order(
            list(manifest.get("file_ids", [])) + list(file_ids)
        )

    if sources:
        manifest["sources"] = _dedupe_keep_order(
            list(manifest.get("sources", [])) + list(sources)
        )

    write_manifest(path, manifest)
    return manifest
