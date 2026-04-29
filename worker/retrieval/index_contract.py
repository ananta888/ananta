from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "worker" / "retrieval_index_contract.v1.json"


def build_index_entry(
    *,
    path: str,
    chunk_id: str,
    text: str,
    embedding: list[float],
    embedding_version: str,
    source_hash: str,
    language: str = "text",
    symbol_name: str = "",
    start_byte: int = 0,
    end_byte: int = 0,
) -> dict[str, Any]:
    entry = {
        "schema": "retrieval_index_entry.v1",
        "chunk_id": str(chunk_id or "").strip(),
        "path": str(path or "").strip(),
        "text": str(text or ""),
        "language": str(language or "text").strip().lower() or "text",
        "symbol_name": str(symbol_name or "").strip(),
        "start_byte": int(start_byte),
        "end_byte": int(end_byte),
        "source_hash": str(source_hash or "").strip(),
        "embedding_version": str(embedding_version or "").strip(),
        "embedding": [float(item) for item in list(embedding or [])],
    }
    validate_index_entry(entry)
    return entry


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_index_entry(entry: dict[str, Any]) -> None:
    errors = list(_schema_validator().iter_errors(dict(entry or {})))
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(item) for item in first.path) or "<root>"
    raise ValueError(f"retrieval_index_entry_invalid:{path}:{first.message}")


def validate_index_entries(entries: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in list(entries or []):
        validate_index_entry(item)
        chunk_id = str(item.get("chunk_id") or "").strip()
        if chunk_id in seen:
            raise ValueError(f"retrieval_index_duplicate_chunk:{chunk_id}")
        seen.add(chunk_id)

