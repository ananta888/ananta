from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from worker.retrieval.vector_encoding import VectorEncoder, VectorEncodingProfile
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreClosedError,
    VectorStoreDiagnostic,
    VectorStoreDimensionsMismatch,
    VectorStoreError,
)

_BACKEND_VERSION = "json-vector-store.v1"


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return float(numerator / (left_norm * right_norm))


class JsonVectorStore:
    """Deterministic reference backend over already prepared vector points."""

    def __init__(
        self,
        *,
        index_path: str | Path,
        legacy_scope: VectorScope | None = None,
    ) -> None:
        self._index_path = Path(index_path)
        self._legacy_scope = legacy_scope
        self._closed = False
        self._cached_signature: tuple[int, int, int, int] | None = None
        self._cached_payload: dict[str, Any] | None = None
        self._last_diagnostic = VectorStoreDiagnostic(
            status="degraded",
            reason="not_loaded",
            provider="json",
            backend_version=_BACKEND_VERSION,
        )

    @property
    def index_path(self) -> Path:
        return self._index_path

    def _ensure_open(self) -> None:
        if self._closed:
            raise VectorStoreClosedError()

    def _index_signature(self) -> tuple[int, int, int, int] | None:
        try:
            stat = self._index_path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _load_snapshot(self) -> dict[str, Any]:
        self._ensure_open()
        signature = self._index_signature()
        if signature is None:
            self._cached_signature = None
            self._cached_payload = None
            self._last_diagnostic = VectorStoreDiagnostic(
                status="degraded",
                reason="missing_index",
                provider="json",
                backend_version=_BACKEND_VERSION,
            )
            return {"state": {}, "entries": []}
        if signature == self._cached_signature and self._cached_payload is not None:
            payload = self._cached_payload
        else:
            try:
                payload = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._last_diagnostic = VectorStoreDiagnostic(
                    status="degraded",
                    reason="invalid_index",
                    provider="json",
                    backend_version=_BACKEND_VERSION,
                )
                raise VectorStoreError("invalid_index") from exc
            if not isinstance(payload, dict):
                raise VectorStoreError("invalid_index")
            self._cached_signature = signature
            self._cached_payload = payload
        state = dict(payload.get("state") or {})
        entries = [item for item in list(payload.get("entries") or []) if isinstance(item, dict)]
        schema = str(state.get("schema") or "")
        reason = "ok" if schema else "migration_required"
        status = "ready" if schema else "degraded"
        self._last_diagnostic = VectorStoreDiagnostic(
            status=status,
            reason=reason,
            provider="json",
            backend_version=_BACKEND_VERSION,
            details=self._state_diagnostics(state, entry_count=len(entries)),
        )
        return {"state": state, "entries": entries}

    def load(self) -> dict[str, Any]:
        """Load an isolated caller-owned copy of the current persisted index."""

        return deepcopy(self._load_snapshot())

    def save(self, *, state: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> None:
        self._ensure_open()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"state": dict(state or {}), "entries": [dict(item) for item in entries]}
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._index_path.parent,
                prefix=f".{self._index_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._index_path)
            temporary_path = None
            self._cached_payload = payload
            self._cached_signature = self._index_signature()
            try:
                directory_fd = os.open(self._index_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        self._last_diagnostic = VectorStoreDiagnostic(
            status="ready",
            reason="ok",
            provider="json",
            backend_version=_BACKEND_VERSION,
            details=self._state_diagnostics(dict(state or {}), entry_count=len(payload["entries"])),
        )

    def diagnostics(self) -> VectorStoreDiagnostic:
        if self._closed:
            return VectorStoreDiagnostic(
                status="closed",
                reason="vector_store_closed",
                provider="json",
                backend_version=_BACKEND_VERSION,
            )
        if self._last_diagnostic.reason in {"dimensions_mismatch", "invalid_index"}:
            return self._last_diagnostic
        self.load()
        return self._last_diagnostic

    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        state = self._compatibility_state(compatibility)
        return self.replace_prepared(points, state=state)

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        current = self.load()
        reason = self._compatibility_reason(dict(current.get("state") or {}), compatibility)
        if reason == "unchanged":
            state = dict(current.get("state") or {})
            return IndexWriteResult(
                status="ok",
                mode="unchanged",
                reason="unchanged",
                indexed_documents=0,
                diagnostics=self._state_diagnostics(state, entry_count=len(current.get("entries") or [])),
                accepted=len(points),
            )
        result = self.rebuild(points, compatibility=compatibility)
        return IndexWriteResult(
            status=result.status,
            mode=result.mode,
            reason=reason,
            indexed_documents=result.indexed_documents,
            diagnostics=result.diagnostics,
            upserted=result.upserted,
            deleted=result.deleted,
            skipped=result.skipped,
            failed=result.failed,
            accepted=result.accepted,
        )

    def replace_prepared(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        state: Mapping[str, Any],
        vector_encoder: VectorEncoder | None = None,
    ) -> IndexWriteResult:
        self._ensure_open()
        point_list = list(points)
        expected = int(dict(state).get("embedding_dimensions") or 0)
        if expected <= 0 and point_list:
            expected = len(point_list[0].vector)
        self._assert_point_dimensions(point_list, expected)
        encoder = vector_encoder or VectorEncoder(VectorEncodingProfile.disabled())
        entries: list[dict[str, Any]] = []
        original_bytes = 0
        encoded_bytes = 0
        max_abs_error = 0.0
        for point in point_list:
            encoded = encoder.encode(list(point.vector))
            original_bytes += int(encoded.diagnostics.get("bytes_original_float32") or len(point.vector) * 4)
            encoded_bytes += int(encoded.diagnostics.get("bytes_encoded_payload") or 0)
            max_abs_error = max(max_abs_error, float(encoded.diagnostics.get("max_abs_error") or 0.0))
            entry = self._entry_from_point(point)
            if encoder.profile.enabled:
                entry["encoded_vector"] = encoded.as_dict()
                if encoder.profile.store_original:
                    entry["vector"] = list(point.vector)
            else:
                entry["vector"] = list(point.vector)
                entry["encoded_vector"] = encoded.as_dict()
            entries.append(entry)
        compression_ratio = round(float(original_bytes) / float(max(1, encoded_bytes)), 4) if original_bytes else 1.0
        next_state = {
            **dict(state or {}),
            "entry_count": len(entries),
            "embedding_dimensions": expected,
            "vector_encoding_profile": encoder.profile.as_dict(),
            "vector_encoding_config_hash": str(
                dict(state or {}).get("vector_encoding_config_hash") or encoder.profile.config_hash()
            ),
            "vector_encoding_compression_ratio": compression_ratio,
            "vector_encoding_max_abs_error": max_abs_error,
        }
        self.save(state=next_state, entries=entries)
        diagnostics = self._state_diagnostics(next_state, entry_count=len(entries))
        return IndexWriteResult(
            status="ok",
            mode="rebuild",
            reason="rebuild",
            indexed_documents=len(entries),
            diagnostics=diagnostics,
            upserted=len(entries),
            accepted=len(point_list),
        )

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        self._ensure_open()
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise VectorStoreError("vector_batch_size_invalid")
        point_list = list(points)
        loaded = self._load_snapshot()
        state = dict(loaded.get("state") or {})
        entries = [dict(item) for item in list(loaded.get("entries") or [])]
        expected = int(state.get("embedding_dimensions") or (len(point_list[0].vector) if point_list else 0))
        self._assert_point_dimensions(point_list, expected)
        by_key = {self._entry_key(entry): entry for entry in entries}
        upserted = 0
        skipped = 0
        for point in point_list:
            entry = self._entry_from_point(point)
            entry["vector"] = list(point.vector)
            existing = by_key.get(self._point_key(point))
            if existing == entry:
                skipped += 1
                continue
            by_key[self._point_key(point)] = entry
            upserted += 1
        next_entries = list(by_key.values())
        next_state = {
            **state,
            "schema": str(state.get("schema") or "vector_store_json.v1"),
            "embedding_dimensions": expected,
            "entry_count": len(next_entries),
        }
        self.save(state=next_state, entries=next_entries)
        return IndexWriteResult(
            status="ok",
            mode="upsert",
            reason="upsert",
            indexed_documents=upserted,
            diagnostics=self._state_diagnostics(next_state, entry_count=len(next_entries)),
            upserted=upserted,
            skipped=skipped,
            accepted=len(point_list),
        )

    def delete(
        self,
        point_ids: Sequence[str],
        *,
        scope: VectorScope,
    ) -> IndexWriteResult:
        self._ensure_open()
        requested = {str(point_id).strip() for point_id in point_ids if str(point_id).strip()}
        loaded = self.load()
        state = dict(loaded.get("state") or {})
        kept: list[dict[str, Any]] = []
        deleted = 0
        for entry in list(loaded.get("entries") or []):
            identity = str(entry.get("_point_id") or entry.get("record_id") or "")
            if identity in requested and self._scope_matches(entry, scope):
                deleted += 1
            else:
                kept.append(dict(entry))
        next_state = {**state, "entry_count": len(kept)}
        self.save(state=next_state, entries=kept)
        return IndexWriteResult(
            status="ok",
            mode="delete",
            reason="delete",
            indexed_documents=0,
            diagnostics=self._state_diagnostics(next_state, entry_count=len(kept)),
            deleted=deleted,
            accepted=len(requested),
        )

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        self._ensure_open()
        if not isinstance(scope, VectorScope):
            raise VectorStoreError("vector_scope_required")
        loaded = self.load()
        state = dict(loaded.get("state") or {})
        entries = [dict(entry) for entry in list(loaded.get("entries") or [])]
        kept = [entry for entry in entries if not self._scope_matches(entry, scope)]
        deleted = len(entries) - len(kept)
        next_state = {**state, "entry_count": len(kept)}
        self.save(state=next_state, entries=kept)
        return IndexWriteResult(
            status="ok",
            mode="delete",
            reason="deleted" if deleted else "empty_scope",
            indexed_documents=0,
            diagnostics=self._state_diagnostics(next_state, entry_count=len(kept)),
            deleted=deleted,
            accepted=1,
        )

    def search_by_vector(
        self,
        query: VectorSearchQuery,
        *,
        vector_encoder: VectorEncoder | None = None,
    ) -> VectorSearchResult:
        self._ensure_open()
        if not isinstance(query.scope, VectorScope):
            diagnostics = {
                "status": "degraded",
                "reason": "vector_scope_required",
            }
            return VectorSearchResult(
                hits=(),
                diagnostics=diagnostics,
                requested_provider="json",
                effective_provider="json",
                reason="vector_scope_required",
            )
        loaded = self._load_snapshot()
        state = dict(loaded.get("state") or {})
        entries = list(loaded.get("entries") or [])
        expected_compatibility = getattr(query, "compatibility", None)
        if expected_compatibility is not None:
            compatibility_reason = self._compatibility_reason(
                state,
                expected_compatibility,
            )
            if compatibility_reason != "unchanged":
                reason = "missing_index" if compatibility_reason == "missing_index" else "fallback_state_incompatible"
                diagnostics = self._state_diagnostics(state, entry_count=len(entries))
                diagnostics.update(
                    {
                        "status": "degraded",
                        "reason": reason,
                        "compatibility_reason": compatibility_reason,
                    }
                )
                return VectorSearchResult(
                    hits=(),
                    diagnostics=diagnostics,
                    requested_provider="json",
                    effective_provider="json",
                    reason=reason,
                )
        expected = int(state.get("embedding_dimensions") or 0)
        if expected and len(query.query_vector) != expected:
            self._set_dimensions_mismatch(expected, len(query.query_vector))
        profile_data = dict(state.get("vector_encoding_profile") or {})
        encoder = vector_encoder or VectorEncoder(VectorEncodingProfile.from_config(profile_data))
        hits: list[VectorSearchHit] = []
        for entry in entries:
            if not self._matches(entry, query):
                continue
            vector = self._entry_vector(entry, encoder)
            point_expected = expected or len(query.query_vector)
            if len(vector) != point_expected:
                self._set_dimensions_mismatch(point_expected, len(vector))
            metadata = dict(entry.get("metadata") or {})
            metadata["vector_encoding_mode"] = encoder.profile.mode
            if encoder.profile.experimental:
                metadata["vector_encoding_experimental"] = "true"
            payload = {
                key: value for key, value in entry.items() if key not in {"vector", "encoded_vector", "_point_id"}
            }
            payload["metadata"] = metadata
            hits.append(
                VectorSearchHit(
                    record_id=str(entry.get("record_id") or ""),
                    score=_cosine_similarity(query.query_vector, vector),
                    payload=payload,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        diagnostics = self._state_diagnostics(state, entry_count=len(entries))
        diagnostics.update({"matched_entries": len(hits), "top_k": query.top_k})
        return VectorSearchResult(
            hits=tuple(hits[: query.top_k]),
            diagnostics=diagnostics,
            requested_provider="json",
            effective_provider="json",
            reason="ok" if entries else "empty_index",
        )

    def compatibility_reason(self, compatibility: CompatibilitySpec) -> str:
        """Return the stable compatibility reason for the current persisted state."""

        self._ensure_open()
        loaded = self.load()
        state = dict(loaded.get("state") or {})
        if not state:
            return "missing_index"
        stored_distance = str(state.get("distance") or "cosine").strip().lower()
        if compatibility.distance != "cosine" or stored_distance != "cosine":
            return "fallback_state_incompatible"
        return self._compatibility_reason(state, compatibility)

    def close(self) -> None:
        self._closed = True

    def _set_dimensions_mismatch(self, expected: int, actual: int) -> None:
        self._last_diagnostic = VectorStoreDiagnostic(
            status="degraded",
            reason="dimensions_mismatch",
            provider="json",
            backend_version=_BACKEND_VERSION,
            details={"expected_dimensions": int(expected), "actual_dimensions": int(actual)},
        )
        raise VectorStoreDimensionsMismatch(expected=expected, actual=actual)

    @staticmethod
    def _assert_point_dimensions(points: Sequence[PreparedVectorPoint], expected: int) -> None:
        for point in points:
            if expected > 0 and len(point.vector) != expected:
                raise VectorStoreDimensionsMismatch(expected=expected, actual=len(point.vector))

    @staticmethod
    def _entry_from_point(point: PreparedVectorPoint) -> dict[str, Any]:
        return {
            **dict(point.payload),
            "record_id": point.record_id,
            "source_hash": point.source_hash,
            "_point_id": point.point_id or point.record_id,
            **point.scope.as_dict(),
        }

    @staticmethod
    def _point_key(point: PreparedVectorPoint) -> tuple[str, str, str, str, str]:
        return (
            point.scope.workspace_id,
            point.scope.repository_id,
            point.scope.profile_name,
            point.scope.domain,
            point.point_id or point.record_id,
        )

    @staticmethod
    def _entry_key(entry: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(entry.get("workspace_id") or ""),
            str(entry.get("repository_id") or ""),
            str(entry.get("profile_name") or "default"),
            str(entry.get("domain") or "codecompass"),
            str(entry.get("_point_id") or entry.get("record_id") or ""),
        )

    def _scope_matches(
        self,
        entry: Mapping[str, Any],
        scope: VectorScope,
    ) -> bool:
        if (
            self._legacy_scope == scope
            and not str(entry.get("workspace_id") or "").strip()
            and not str(entry.get("repository_id") or "").strip()
        ):
            return True
        return (
            str(entry.get("workspace_id") or "") == scope.workspace_id
            and str(entry.get("repository_id") or "") == scope.repository_id
            and str(entry.get("profile_name") or "default") == scope.profile_name
            and str(entry.get("domain") or "codecompass") == scope.domain
        )

    def _matches(
        self,
        entry: Mapping[str, Any],
        query: VectorSearchQuery,
    ) -> bool:
        scope = query.scope
        if not isinstance(scope, VectorScope):
            return False
        if not self._scope_matches(entry, scope):
            return False
        filters = query.filters
        if filters is None:
            return True
        if filters.source_scope and str(entry.get("source_scope") or "") != filters.source_scope:
            return False
        if filters.profile_name and str(entry.get("profile_name") or "") != filters.profile_name:
            return False
        if filters.kinds and str(entry.get("kind") or "") not in set(filters.kinds):
            return False
        if filters.file_prefix:
            source = str(entry.get("file") or "").strip().lstrip("./")
            prefix = filters.file_prefix.strip().rstrip("/").lstrip("./")
            if source != prefix and not source.startswith(f"{prefix}/"):
                return False
        if filters.role_labels:
            labels = {str(item) for item in list(entry.get("role_labels") or [])}
            if not set(filters.role_labels).issubset(labels):
                return False
        return True

    @staticmethod
    def _entry_vector(entry: Mapping[str, Any], encoder: VectorEncoder) -> list[float]:
        if "vector" in entry:
            return [float(item) for item in list(entry.get("vector") or [])]
        encoded = entry.get("encoded_vector")
        if isinstance(encoded, dict):
            return encoder.decode(encoded)
        return []

    @staticmethod
    def _compatibility_state(spec: CompatibilitySpec) -> dict[str, Any]:
        return {
            "schema": spec.schema_version,
            "distance": spec.distance,
            "embedding_provider": spec.provider,
            "embedding_model_name": spec.model,
            "embedding_dimensions": spec.dimensions,
            "embedding_provider_config_hash": spec.config_hash,
            "embedding_text_profile": spec.profile,
            "vector_encoding_config_hash": spec.encoding,
            "manifest_hash": spec.manifest_hash,
        }

    @staticmethod
    def _compatibility_reason(state: Mapping[str, Any], spec: CompatibilitySpec) -> str:
        if not state:
            return "missing_index"
        checks = (
            ("schema", spec.schema_version, "schema_changed"),
            ("distance", spec.distance, "distance_changed"),
            ("embedding_provider", spec.provider, "provider_changed"),
            ("embedding_model_name", spec.model, "provider_changed"),
            ("embedding_dimensions", spec.dimensions, "dimensions_changed"),
            ("embedding_provider_config_hash", spec.config_hash, "provider_config_changed"),
            ("embedding_text_profile", spec.profile, "embedding_text_profile_changed"),
            ("vector_encoding_config_hash", spec.encoding, "vector_encoding_changed"),
            ("manifest_hash", spec.manifest_hash, "manifest_changed"),
        )
        for field_name, expected, reason in checks:
            found = state.get(field_name)
            if field_name == "embedding_dimensions":
                if int(found or 0) != int(expected):
                    return reason
            elif str(found or "") != str(expected or ""):
                return reason
        return "unchanged"

    @staticmethod
    def _state_diagnostics(state: Mapping[str, Any], *, entry_count: int) -> dict[str, Any]:
        return {
            "entry_count": int(state.get("entry_count") or entry_count),
            "provider": str(state.get("embedding_provider") or ""),
            "model": str(state.get("embedding_model_name") or ""),
            "dimensions": int(state.get("embedding_dimensions") or 0),
            "distance": str(state.get("distance") or "cosine"),
            "manifest_hash": str(state.get("manifest_hash") or ""),
            "embedding_text_profile": str(state.get("embedding_text_profile") or ""),
            "embedding_provider_config_hash": str(state.get("embedding_provider_config_hash") or ""),
            "schema_version": str(state.get("schema") or ""),
            "backend": "json",
            "backend_version": _BACKEND_VERSION,
        }


__all__ = ["JsonVectorStore"]
