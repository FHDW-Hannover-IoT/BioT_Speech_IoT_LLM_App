# RAG Functions Overview

This document explains the functions and methods inside the `rag/` folder.

## rag/advanced_rag.py

### Data classes

- `SearchResult`
  - `text`: Extracted passage text
  - `score`: Relevance score if provided by the API
  - `file_id`: OpenAI file id
  - `filename`: Original filename if available
- `RagAnswer`
  - `answer`: Final response text
  - `sources`: List of source labels used in the answer

### Helper functions

- `_get_client()`
  - Creates an OpenAI client using `OPENAI_API_KEY`.
- `_safe_json_loads(text)`
  - Parses JSON safely from model output; returns empty dict on failure.
- `_to_dict(value)`
  - Best-effort conversion of SDK objects to plain dict.
- `_content_to_text(content)`
  - Normalizes SDK content blocks into plain text.
- `_write_manifest(path, store_id, file_ids, sources)`
  - Writes a JSON manifest with vector store id and file ids.
- `_read_manifest(path)`
  - Reads the manifest JSON into a dict.
- `_cmd_ingest(args)`
  - CLI action: uploads files, creates/updates vector store, writes manifest.
- `_cmd_query(args)`
  - CLI action: runs a query and prints JSON output.
- `_build_parser()`
  - Builds the CLI parser with `ingest` and `query` subcommands.
- `main()`
  - CLI entry point.

### `AdvancedRag` class

- `__init__(client=None, model=None, rerank_model=None, use_llm_rerank=True)`
  - Configures the OpenAI client and model selection.
- `generate_queries(question, max_queries=4)`
  - Uses the LLM to generate multiple search queries for retrieval.
- `_vector_store_search(vector_store_id, query, top_k)`
  - Calls the OpenAI vector store search endpoint with best-effort params.
- `_parse_search_results(response)`
  - Extracts `SearchResult` objects from SDK response data.
- `retrieve(question, vector_store_id, top_k=6, max_candidates=24)`
  - Multi-query retrieval + dedupe + optional rerank.
- `rerank(question, candidates, top_k=6)`
  - LLM-based reranker that returns the top ranked passages.
- `answer(question, vector_store_id, top_k=6, max_context_chars=8000)`
  - Builds a context window and asks the LLM to answer with citations.

## rag/create_vector_store.py

### Helper functions

- `_get_client()`
  - Creates an OpenAI client using `LLM_API_KEY`.
- `_normalize_file_ids(file_ids)`
  - Normalizes file id input into a list of strings.
- `_wait_for_processing(client, vector_store_id, timeout_secs=120, poll_interval_secs=2.0)`
  - Polls until vector store files are processed or timeout occurs.

### Public functions

- `add_files_to_vector_store(vector_store_id, file_ids, wait=True)`
  - Attaches files to a vector store and optionally waits for processing.
- `create_vector_store(store_name, file_ids=None, wait=True, manifest_path=None)`
  - Creates a new vector store and optionally attaches files.
- `create_vector_store` updates the manifest if `manifest_path` is provided.
- `delete_vector_store(store_id)`
  - Deletes an existing vector store by id.

### Script usage

- Running the file directly creates a vector store with a hard-coded file id.

## rag/upload_file.py

### Helper functions

- `_get_client()`
  - Creates an OpenAI client using `OPENAI_API_KEY` or fallback `LLM_API_KEY`.
- `_open_url(url)`
  - Downloads a URL into memory and returns `(filename, BytesIO)`.

### Public functions

- `create_file(file_path, purpose="assistants", verbose=True)`
  - Uploads a local file or URL and returns the OpenAI `file_id`.
- `create_file` updates the manifest if `manifest_path` is provided.
- `create_files(file_paths, purpose="assistants", verbose=True, manifest_path=None)`
  - Batch version of `create_file`.
- `delete_file(file_id_to_delete, verbose=True)`
  - Deletes an uploaded file by id.

### Script usage

- Running the file directly uploads `rag/Doku.pdf` and prints the `file_id`.

## rag/manifest.py

### Helper functions

- `read_manifest(path)`
  - Reads and normalizes the manifest JSON.
- `write_manifest(path, data)`
  - Writes a normalized manifest JSON file.
- `update_manifest(path, vector_store_id=None, file_ids=None, sources=None)`
  - Creates or updates a manifest, merging new ids/sources without duplicates.

## Environment variables used by rag/

- `OPENAI_API_KEY` (required by `advanced_rag.py` and preferred by `upload_file.py`)
- `LLM_API_KEY` (fallback in `upload_file.py`, primary in `create_vector_store.py`)
- `OPENAI_RAG_MODEL` (optional default model for `advanced_rag.py`)
- `OPENAI_RERANK_MODEL` (optional default rerank model for `advanced_rag.py`)
- `OPENAI_MODEL` (fallback model in `advanced_rag.py` if `OPENAI_RAG_MODEL` missing)
