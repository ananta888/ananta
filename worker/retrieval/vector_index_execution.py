from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorStore,
    VectorStoreError,
)
from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    VectorStoreEndpointPolicyError,
    VectorStoreSecretError,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory


_SUPPORTED_OPERATIONS = frozenset({"index", "refresh", "rebuild", "delete", "migrate"})


class ConfiguredVectorIndexExecution:
    """Execute one immutable Hub-resolved mutation without orchestration."""

    def __init__(
        self,
        *,
        factory: VectorStoreFactory | None = None,
        secret_resolver: EnvFileSecretResolver | None = None,
        observer: Any = None,
    ) -> None:
        self._factory = factory or VectorStoreFactory()
        self._secret_resolver = secret_resolver or EnvFileSecretResolver()
        self._observer = observer

    def execute(
        self,
        *,
        operation: str,
        scope: Mapping[str, str],
        resolved_config: Mapping[str, Any],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del idempotency_key
        operation_name = str(operation or "").strip().lower()
        if operation_name not in _SUPPORTED_OPERATIONS:
            return self._failed(operation_name, "vector_index_operation_invalid")
        store: VectorStore | None = None
        try:
            trusted_scope = self._scope(scope)
            config = VectorStoreConfig.from_mapping(
                self._configuration(resolved_config)
            )
            store = self._create_store(config)
            result, checkpoint, activated = self._execute_operation(
                store=store,
                operation=operation_name,
                scope=trusted_scope,
                payload=dict(payload),
            )
            terminal = "completed" if result.status == "ok" else "failed"
            result_payload = {**result.as_dict(), "operation": operation_name}
            if checkpoint is not None:
                result_payload["checkpoint"] = dict(checkpoint)
            if activated is not None:
                result_payload["activated"] = bool(activated)
            return {
                "status": terminal,
                "reason_code": result.reason,
                "diagnostics": dict(result.diagnostics),
                "result": result_payload,
            }
        except (
            ImportError,
            OSError,
            TypeError,
            ValueError,
            VectorStoreConfigError,
            VectorStoreEndpointPolicyError,
            VectorStoreSecretError,
            VectorStoreError,
        ) as exc:
            reason = str(getattr(exc, "reason", "") or "vector_index_operation_failed")
            return self._failed(operation_name, reason)
        finally:
            if store is not None:
                store.close()

    def _create_store(self, config: VectorStoreConfig) -> VectorStore:
        if config.provider != VectorStoreProvider.QDRANT:
            return self._factory.create(
                config,
                secret_resolver=self._secret_resolver,
            )
        if config.qdrant is None:
            raise VectorStoreConfigError("missing_qdrant_vector_store_config")
        from worker.retrieval.qdrant_vector_store import QdrantVectorStore

        return QdrantVectorStore.from_config(
            config.qdrant,
            secret_resolver=self._secret_resolver,
            observer=self._observer,
        )

    def _execute_operation(
        self,
        *,
        store: VectorStore,
        operation: str,
        scope: VectorScope,
        payload: Mapping[str, Any],
    ) -> tuple[IndexWriteResult, Mapping[str, Any] | None, bool | None]:
        if operation == "delete":
            result = store.delete(
                tuple(str(item) for item in list(payload.get("point_ids") or ())),
                scope=scope,
            )
            return result, None, None

        if operation == "migrate":
            from worker.retrieval.qdrant_vector_store import QdrantVectorStore
            from worker.retrieval.vector_store_migration import (
                JsonToQdrantMigrator,
                MigrationCheckpoint,
            )

            compatibility = self._compatibility(payload.get("compatibility"))
            if not isinstance(store, QdrantVectorStore):
                raise ValueError("vector_index_migration_requires_qdrant")
            source_value = str(payload.get("source_path") or "").strip()
            if not source_value:
                raise ValueError("vector_index_migration_source_required")
            checkpoint_payload = self._mapping(payload.get("checkpoint"))
            checkpoint = (
                MigrationCheckpoint(
                    source_digest=str(checkpoint_payload.get("source_digest") or ""),
                    collection_name=str(checkpoint_payload.get("collection_name") or ""),
                    next_offset=int(checkpoint_payload.get("next_offset") or 0),
                )
                if checkpoint_payload
                else None
            )
            migrated = JsonToQdrantMigrator(
                store,
                observer=self._observer,
            ).migrate(
                Path(source_value),
                scope=scope,
                compatibility=compatibility,
                checkpoint=checkpoint,
                batch_size=int(payload.get("batch_size") or 128),
                max_batches=(
                    int(payload["max_batches"])
                    if payload.get("max_batches") is not None
                    else None
                ),
            )
            return (
                migrated.result,
                (
                    asdict(migrated.checkpoint)
                    if migrated.checkpoint is not None
                    else None
                ),
                migrated.activated,
            )

        points = self._points(payload.get("points"), scope)
        if operation == "index":
            result = store.upsert(
                points,
                batch_size=int(payload.get("batch_size") or 128),
            )
        else:
            compatibility = self._compatibility(payload.get("compatibility"))
            if operation == "refresh":
                result = store.refresh(points, compatibility=compatibility)
            else:
                result = store.rebuild(points, compatibility=compatibility)
        return result, None, None

    @classmethod
    def _configuration(cls, resolved: Mapping[str, Any]) -> Mapping[str, Any]:
        for field in (
            "config",
            "configuration",
            "effective_config",
            "vector_store",
            "resolved",
        ):
            candidate = resolved.get(field)
            if isinstance(candidate, Mapping):
                return dict(candidate)
        candidate = {
            key: value
            for key, value in resolved.items()
            if key
            in {
                "provider",
                "availability",
                "fail_mode",
                "fallback_provider",
                "json",
                "qdrant",
            }
        }
        if candidate:
            return candidate
        raise ValueError("vector_store_resolved_configuration_missing")

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _scope(value: Mapping[str, Any]) -> VectorScope:
        return VectorScope(
            workspace_id=str(value.get("workspace_id") or ""),
            repository_id=str(value.get("repository_id") or ""),
            profile_name=str(value.get("profile_name") or "default"),
            domain=str(value.get("domain") or "codecompass"),
        )

    @classmethod
    def _compatibility(cls, value: Any) -> CompatibilitySpec:
        payload = cls._mapping(value)
        return CompatibilitySpec(
            dimensions=int(payload.get("dimensions") or 0),
            distance=str(payload.get("distance") or "cosine"),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            profile=str(payload.get("profile") or ""),
            encoding=str(payload.get("encoding") or "float32"),
            config_hash=str(payload.get("config_hash") or ""),
            schema_version=str(payload.get("schema_version") or "vector_store.v1"),
            manifest_hash=str(payload.get("manifest_hash") or ""),
        )

    @classmethod
    def _points(
        cls,
        value: Any,
        trusted_scope: VectorScope,
    ) -> tuple[PreparedVectorPoint, ...]:
        points: list[PreparedVectorPoint] = []
        for item in list(value or ()):
            point = cls._mapping(item)
            supplied_scope = point.get("scope")
            if supplied_scope and cls._scope(cls._mapping(supplied_scope)) != trusted_scope:
                raise ValueError("vector_index_point_scope_mismatch")
            points.append(
                PreparedVectorPoint(
                    record_id=str(point.get("record_id") or ""),
                    point_id=(
                        str(point["point_id"])
                        if point.get("point_id") is not None
                        else None
                    ),
                    vector=tuple(float(number) for number in list(point.get("vector") or ())),
                    scope=trusted_scope,
                    payload=cls._mapping(point.get("payload")),
                    source_hash=str(point.get("source_hash") or ""),
                )
            )
        return tuple(points)

    @staticmethod
    def _failed(operation: str, reason: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "reason_code": str(reason or "vector_index_operation_failed"),
            "diagnostics": {"operation": operation},
            "result": None,
            "error": "vector index operation failed",
        }


__all__ = ["ConfiguredVectorIndexExecution"]
