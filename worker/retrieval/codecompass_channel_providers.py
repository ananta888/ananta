"""Production CodeCompass retrieval-channel composition for worker containers."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from worker.retrieval.codecompass_fts_engine import CodeCompassFtsEngine
from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore


class CodeCompassChannelProvider(Protocol):
    def search(
        self,
        *,
        query: str,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]: ...


class JsonlSymbolProvider:
    """Bounded symbol lookup over worker-produced detail records."""

    _TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,127}")

    def __init__(
        self,
        *,
        paths: Iterable[str | Path],
        maximum_bytes: int = 32 * 1024 * 1024,
        maximum_records: int = 100_000,
    ) -> None:
        self._paths = tuple(sorted({Path(path) for path in paths}, key=lambda value: value.as_posix()))
        self._maximum_bytes = max(1, int(maximum_bytes))
        self._maximum_records = max(1, int(maximum_records))

    def search(
        self,
        *,
        query: str,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
    ) -> list[dict[str, Any]]:
        del task_kind, retrieval_intent
        query_tokens = {token.lower() for token in self._TOKEN.findall(str(query or ""))}
        if not query_tokens:
            return []
        candidates: list[dict[str, Any]] = []
        consumed_bytes = 0
        consumed_records = 0
        for path in self._iter_files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < 0 or consumed_bytes + size > self._maximum_bytes:
                break
            consumed_bytes += size
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for raw_line in lines:
                if consumed_records >= self._maximum_records:
                    break
                consumed_records += 1
                try:
                    record = json.loads(raw_line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                symbol = str(
                    record.get("symbol")
                    or record.get("name")
                    or record.get("class_name")
                    or ""
                ).strip()
                file_path = str(record.get("file") or record.get("path") or "").strip()
                record_id = str(record.get("id") or record.get("record_id") or "").strip()
                haystack = " ".join(
                    str(record.get(key) or "")
                    for key in ("symbol", "name", "class_name", "kind", "file", "path", "summary")
                ).lower()
                matched = [token for token in query_tokens if token in haystack]
                if not matched or not (file_path or record_id):
                    continue
                exact = 1.0 if symbol.lower() in query_tokens else 0.0
                score = exact * 4.0 + float(len(matched)) + 1.0 / float(len(candidates) + 1)
                provenance = dict(record.get("provenance") or {})
                candidates.append(
                    {
                        "path": file_path,
                        "record_id": record_id,
                        "content_hash": str(record.get("content_hash") or record.get("sha256") or ""),
                        "content": str(record.get("summary") or record.get("content") or symbol)[:1000],
                        "score": score,
                        "source_id": str(record.get("source_id") or ""),
                        "source_version": str(record.get("source_version") or ""),
                        "tenant_id": str(record.get("tenant_id") or ""),
                        "scope": str(record.get("scope") or record.get("source_scope") or ""),
                        "provenance": provenance,
                        "provenance_digest": str(record.get("provenance_digest") or ""),
                        "metadata": {
                            "record_id": record_id,
                            "record_kind": str(record.get("record_kind") or record.get("kind") or "symbol"),
                            "symbol": symbol,
                            "file": file_path,
                            "line_start": record.get("line_start", record.get("line")),
                            "line_end": record.get("line_end", record.get("end_line")),
                            "source_manifest_hash": str(record.get("manifest_hash") or ""),
                            "source_id": str(record.get("source_id") or ""),
                            "source_version": str(record.get("source_version") or ""),
                            "tenant_id": str(record.get("tenant_id") or ""),
                            "scope": str(record.get("scope") or record.get("source_scope") or ""),
                            "provenance": provenance,
                            "provenance_digest": str(record.get("provenance_digest") or ""),
                        },
                    }
                )
            if consumed_records >= self._maximum_records:
                break
        candidates.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("path") or ""),
                str(item.get("record_id") or ""),
            )
        )
        return candidates[: max(1, int(top_k))]

    def _iter_files(self) -> Iterable[Path]:
        for path in self._paths:
            if path.is_file() and not path.is_symlink():
                yield path
            elif path.is_dir() and not path.is_symlink():
                yield from sorted(
                    (
                        candidate
                        for candidate in path.rglob("*.jsonl")
                        if candidate.is_file() and not candidate.is_symlink()
                    ),
                    key=lambda value: value.as_posix(),
                )


def providers_from_environment(
    *,
    provider_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, CodeCompassChannelProvider], CodeCompassGraphStore | None, dict[str, str]]:
    """Compose only explicitly mounted worker indexes; never read Hub paths."""

    providers: dict[str, CodeCompassChannelProvider] = {}
    diagnostics: dict[str, str] = {}

    fts_path = _existing_file("ANANTA_CODECOMPASS_FTS_DB")
    if fts_path is not None:
        providers["codecompass_fts"] = CodeCompassFtsEngine(
            store=CodeCompassFtsStore(db_path=fts_path)
        )
    else:
        diagnostics["codecompass_fts"] = "fts_index_not_mounted"

    vector_path = _existing_file("ANANTA_CODECOMPASS_VECTOR_INDEX")
    if vector_path is not None:
        providers["codecompass_vector"] = CodeCompassVectorEngine.build_from_config(
            CodeCompassVectorStore(index_path=vector_path),
            provider_config=dict(provider_config or {}),
        )
    else:
        diagnostics["codecompass_vector"] = "vector_index_not_mounted"

    symbol_paths = [
        Path(item.strip())
        for item in str(os.environ.get("ANANTA_CODECOMPASS_SYMBOL_PATHS") or "").split(os.pathsep)
        if item.strip()
    ]
    existing_symbol_paths = [path for path in symbol_paths if path.exists() and not path.is_symlink()]
    if existing_symbol_paths:
        providers["symbol"] = JsonlSymbolProvider(paths=existing_symbol_paths)
    else:
        diagnostics["symbol"] = "symbol_index_not_mounted"

    graph_path = _existing_file("ANANTA_CODECOMPASS_GRAPH_INDEX")
    graph_store = CodeCompassGraphStore(index_path=graph_path) if graph_path is not None else None
    if graph_store is None:
        diagnostics["codecompass_graph"] = "graph_index_not_mounted"
    return providers, graph_store, diagnostics


def _existing_file(environment_name: str) -> Path | None:
    raw = str(os.environ.get(environment_name) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_symlink() or not path.is_file():
        return None
    return path


__all__ = [
    "CodeCompassChannelProvider",
    "JsonlSymbolProvider",
    "providers_from_environment",
]
