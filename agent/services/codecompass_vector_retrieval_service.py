from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.embedding_provider_config_service import (
    EmbeddingProviderConfigService,
    build_embedding_provider_from_config,
)
from worker.retrieval.codecompass_embedding_loader import load_codecompass_embedding_documents
from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.embedding_text_builder import CODECOMPASS_EMBEDDING_TEXT_PROFILE


class CodeCompassVectorRetrievalService:
    """Agent-side adapter for CodeCompass vector retrieval.

    The service owns file loading, provider resolution and index refresh. The
    worker package still owns embedding/vector execution.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        embedding_records_path: str | Path,
        manifest_path: str | Path,
        index_path: str | Path,
        provider_config: dict[str, Any] | None = None,
        embedding_text_profile: str = CODECOMPASS_EMBEDDING_TEXT_PROFILE,
        fail_mode: str = "degraded_empty",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.embedding_records_path = self._resolve_path(embedding_records_path)
        self.manifest_path = self._resolve_path(manifest_path)
        self.store = CodeCompassVectorStore(index_path=self._resolve_path(index_path))
        self.provider_config = dict(provider_config or {})
        self.embedding_text_profile = str(embedding_text_profile or CODECOMPASS_EMBEDDING_TEXT_PROFILE)
        self.fail_mode = str(fail_mode or "degraded_empty")
        self._last_diagnostic: dict[str, Any] = {"status": "not_run", "reason": "not_run"}

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path

    def last_diagnostic(self) -> dict[str, Any]:
        return dict(self._last_diagnostic)

    def search(self, *, query: str, top_k: int = 10, allowed_paths: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            documents, manifest, load_diagnostics = self._load_documents()
            provider_service = EmbeddingProviderConfigService(global_config=self.provider_config)
            provider_cfg = provider_service.resolve("codecompass_vector")
            provider = build_embedding_provider_from_config(provider_cfg)
            refresh = self.store.refresh(
                documents=documents,
                embedding_provider=provider,
                retrieval_cache_state=str(manifest.get("retrieval_cache_state") or ""),
                manifest_hash=str(manifest.get("manifest_hash") or ""),
                embedding_provider_config_hash=provider_cfg.config_hash(),
                embedding_text_profile=self.embedding_text_profile,
            )
            engine = CodeCompassVectorEngine(store=self.store, embedding_provider=provider)
            rows = engine.search(query=query, top_k=max(1, int(top_k)), retrieval_intent="fuzzy_semantic")
            rows = self._filter_allowed_paths(rows, allowed_paths)
            self._last_diagnostic = {
                "status": "ready",
                "reason": refresh.get("reason", "ok"),
                "candidate_count": len(rows),
                "load": load_diagnostics,
                "refresh": refresh.get("diagnostics", {}),
                "engine": engine.last_diagnostic(),
            }
            return rows
        except Exception as exc:
            if self.fail_mode != "degraded_empty":
                raise
            self._last_diagnostic = {
                "status": "degraded",
                "reason": self._classify_exception(exc),
                "error": str(exc),
            }
            return []

    def _load_documents(self) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        if not self.embedding_records_path.exists():
            raise FileNotFoundError(f"missing_embedding_records:{self.embedding_records_path}")
        if not self.manifest_path.exists():
            manifest: dict[str, Any] = {}
        else:
            manifest = self._read_json_object(self.manifest_path)
        records = self._read_records(self.embedding_records_path)
        payload = load_codecompass_embedding_documents(records=records, manifest=manifest)
        documents = list(payload.get("documents") or [])
        if not documents:
            raise ValueError("no_codecompass_embedding_documents")
        return documents, manifest, dict(payload.get("diagnostics") or {})

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected_json_object:{path}")
        return payload

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            records = payload.get("records")
            if isinstance(records, list):
                return [dict(item) for item in records if isinstance(item, dict)]
        raise ValueError(f"expected_embedding_records:{path}")

    @staticmethod
    def _filter_allowed_paths(rows: list[dict[str, Any]], allowed_paths: list[str] | None) -> list[dict[str, Any]]:
        if allowed_paths is None:
            return rows
        prefixes = [str(path).strip().rstrip("/") for path in allowed_paths if str(path).strip()]
        if not prefixes:
            return []
        kept: list[dict[str, Any]] = []
        for row in rows:
            source = str(row.get("source") or row.get("file") or "").strip().lstrip("./")
            if any(source == prefix or source.startswith(f"{prefix}/") for prefix in prefixes):
                kept.append(row)
        return kept

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        text = str(exc)
        if isinstance(exc, FileNotFoundError):
            return "missing_embedding_records"
        if isinstance(exc, json.JSONDecodeError):
            return "invalid_json"
        if text.startswith("embedding_provider_blocked"):
            return "provider_blocked"
        if text.startswith("no_codecompass_embedding_documents"):
            return "no_embedding_documents"
        return "codecompass_vector_unavailable"
