from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from typing import Any, Mapping

from worker.retrieval.embedding_provider import EmbeddingProviderError
from worker.retrieval.vector_index_input_loader import (
    BoundedVectorIndexInputLoader,
    VectorIndexInputError,
)
from worker.retrieval.vector_index_preparation import (
    VectorIndexPreparationService,
)
from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PlannedVectorIndexWriter,
    PreparedVectorPoint,
    VectorIndexWritePlan,
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
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class ConfiguredVectorIndexExecution:
    """Execute one immutable Hub-resolved mutation without orchestration."""

    def __init__(
        self,
        *,
        factory: VectorStoreFactory | None = None,
        secret_resolver: EnvFileSecretResolver | None = None,
        input_loader: BoundedVectorIndexInputLoader | None = None,
        preparation_service: VectorIndexPreparationService | None = None,
        observer: Any = None,
    ) -> None:
        self._factory = factory or VectorStoreFactory()
        self._secret_resolver = secret_resolver or EnvFileSecretResolver()
        self._input_loader = input_loader or BoundedVectorIndexInputLoader()
        self._preparation_service = (
            preparation_service or VectorIndexPreparationService()
        )
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
        operation_name = str(operation or "").strip().lower()
        if operation_name not in _SUPPORTED_OPERATIONS:
            return self._failed(operation_name, "vector_index_operation_invalid")
        normalized_idempotency_key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY_KEY.fullmatch(normalized_idempotency_key) is None:
            return self._failed(
                operation_name,
                "vector_index_task_idempotency_key_invalid",
            )
        store: VectorStore | None = None
        try:
            trusted_scope = self._scope(scope)
            operation_payload = dict(payload)
            self._validate_input_binding(
                operation=operation_name,
                payload=operation_payload,
                trusted_scope=trusted_scope,
            )
            preparation = operation_payload.get("preparation")
            if preparation is not None:
                if not isinstance(preparation, Mapping):
                    raise ValueError(
                        "vector_index_preparation_invalid"
                    )
                self._preparation_service.validate_embedding_egress(
                    preparation
                )
            config = VectorStoreConfig.from_mapping(
                self._configuration(resolved_config)
            )
            store = self._create_store(config)
            result, checkpoint, activated = self._execute_operation(
                store=store,
                operation=operation_name,
                scope=trusted_scope,
                payload=operation_payload,
                idempotency_key=normalized_idempotency_key,
            )
            terminal = "completed" if result.status == "ok" else "failed"
            result_payload = {**result.as_dict(), "operation": operation_name}
            result_payload["idempotency_key_hash"] = hashlib.sha256(
                normalized_idempotency_key.encode("utf-8")
            ).hexdigest()
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
            VectorIndexInputError,
            VectorStoreError,
            EmbeddingProviderError,
        ) as exc:
            reason = self._exception_reason(exc)
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
        idempotency_key: str,
    ) -> tuple[IndexWriteResult, Mapping[str, Any] | None, bool | None]:
        if operation == "delete":
            delete_all_scope = payload.get("delete_all_scope", False)
            if not isinstance(delete_all_scope, bool):
                raise ValueError("vector_index_delete_all_scope_invalid")
            if delete_all_scope:
                if payload.get("point_ids"):
                    raise ValueError("vector_index_delete_selector_ambiguous")
                result = store.delete_scope(scope)
            else:
                result = store.delete(
                    tuple(
                        str(item)
                        for item in list(payload.get("point_ids") or ())
                    ),
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
            migration_payload = self._mapping(payload.get("migration"))
            input_ref = self._mapping(payload.get("input_ref"))
            if not input_ref:
                raise ValueError("vector_index_migration_source_required")
            source = self._input_loader.load_bytes(
                input_ref,
                trusted_scope=scope,
            )
            checkpoint_payload = self._mapping(
                migration_payload.get("checkpoint")
            )
            checkpoint = (
                MigrationCheckpoint(
                    source_digest=str(checkpoint_payload.get("source_digest") or ""),
                    collection_name=str(checkpoint_payload.get("collection_name") or ""),
                    next_offset=int(checkpoint_payload.get("next_offset") or 0),
                    scope_fingerprint=str(
                        checkpoint_payload.get("scope_fingerprint") or ""
                    ),
                    idempotency_key_hash=str(
                        checkpoint_payload.get("idempotency_key_hash") or ""
                    ),
                )
                if checkpoint_payload
                else None
            )
            migrator = JsonToQdrantMigrator(
                store,
                observer=self._observer,
            )
            if bool(migration_payload.get("dry_run", False)):
                plan = migrator.dry_run(
                    source,
                    scope=scope,
                    compatibility=compatibility,
                )
                result = IndexWriteResult(
                    status="ok" if plan.status == "ready" else "failed",
                    mode="migrate_dry_run",
                    reason=plan.reason,
                    indexed_documents=0,
                    diagnostics=asdict(plan),
                    failed=(
                        0
                        if plan.status == "ready"
                        else plan.source_entries
                    ),
                )
                return result, None, False
            migrated = migrator.migrate(
                source,
                scope=scope,
                compatibility=compatibility,
                checkpoint=checkpoint,
                batch_size=self._batch_size(payload.get("batch_size")),
                max_batches=(
                    int(migration_payload["max_batches"])
                    if migration_payload.get("max_batches") is not None
                    else None
                ),
                idempotency_key=idempotency_key,
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

        inline_points = payload.get("points")
        input_ref = self._mapping(payload.get("input_ref"))
        preparation = self._mapping(payload.get("preparation"))
        if inline_points and input_ref:
            raise ValueError("vector_index_input_ambiguous")
        if preparation:
            if inline_points or not input_ref:
                raise ValueError(
                    "vector_index_preparation_input_invalid"
                )
            compatibility = self._compatibility(
                payload.get("compatibility")
            )
            points = self._preparation_service.prepare(
                document_input=self._input_loader.load_document_input(
                    input_ref,
                    trusted_scope=scope,
                ),
                scope=scope,
                compatibility=compatibility,
                preparation=preparation,
            )
        else:
            point_payload = (
                self._input_loader.load_points(
                    input_ref,
                    trusted_scope=scope,
                )
                if input_ref
                else inline_points
            )
            points = self._points(point_payload, scope)
        if operation == "index":
            result = store.upsert(
                points,
                batch_size=self._batch_size(payload.get("batch_size")),
            )
        else:
            compatibility = self._compatibility(
                payload.get("compatibility")
            )
            plan = VectorIndexWritePlan(
                batch_size=self._batch_size(payload.get("batch_size")),
            )
            if isinstance(store, PlannedVectorIndexWriter):
                if operation == "refresh":
                    result = store.refresh_with_plan(
                        points,
                        compatibility=compatibility,
                        plan=plan,
                    )
                else:
                    result = store.rebuild_with_plan(
                        points,
                        compatibility=compatibility,
                        plan=plan,
                    )
            elif operation == "refresh":
                result = store.refresh(points, compatibility=compatibility)
            else:
                result = store.rebuild(points, compatibility=compatibility)
        return result, None, None

    def _validate_input_binding(
        self,
        *,
        operation: str,
        payload: Mapping[str, Any],
        trusted_scope: VectorScope,
    ) -> None:
        """Fail before store construction for every referenced input."""

        migration = payload.get("migration")
        if operation == "migrate":
            if not isinstance(migration, Mapping):
                raise ValueError("vector_index_migration_contract_required")
            if set(migration) - {
                "dry_run",
                "checkpoint",
                "max_batches",
            }:
                raise ValueError("vector_index_migration_fields_forbidden")
        input_ref = payload.get("input_ref")
        if input_ref is None:
            if operation == "migrate":
                raise ValueError("vector_index_migration_source_required")
            return
        if not isinstance(input_ref, Mapping):
            raise ValueError("vector_index_input_ref_invalid")
        self._input_loader.validate_reference(
            input_ref,
            trusted_scope=trusted_scope,
        )

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
    def _batch_size(value: Any) -> int:
        if value is None:
            return 128
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("vector_batch_size_invalid")
        if not 1 <= value <= 1000:
            raise ValueError("vector_batch_size_invalid")
        return value

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

    @staticmethod
    def _exception_reason(exc: Exception) -> str:
        explicit = str(getattr(exc, "reason", "") or "").strip()
        if explicit:
            return explicit.split(":", 1)[0]
        candidate = str(exc).split(":", 1)[0].strip()
        if candidate.startswith(
            ("vector_", "qdrant_", "migration_", "embedding_")
        ):
            return candidate
        return "vector_index_operation_failed"


__all__ = ["ConfiguredVectorIndexExecution"]
