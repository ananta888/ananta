"""Hub-side projection of structured results from delegated Unsloth workers."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import PurePosixPath
import re
from typing import Any

from flask import current_app, has_app_context

from ananta_contracts.unsloth_task import (
    unsloth_payload_sha256,
)
from agent.services.unsloth_model_catalog_service import (
    UnslothModelImportResultHandler,
    get_unsloth_model_catalog_registry,
)
from agent.services.unsloth_storage_governance_service import (
    StorageCleanupCompletionPort,
    UnslothStorageError,
    storage_catalog_from_config,
)

UNSLOTH_WORKER_RESULT_SCHEMA = (
    "ananta.unsloth-worker-task-result.v1"
)
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "task_type",
        "tenant_id",
        "payload_sha256",
        "status",
        "reason_code",
        "result",
    }
)
_RECIPE_RESULT_FIELDS = frozenset(
    {
        "schema",
        "recipe_id",
        "attempt_id",
        "dataset_hash",
        "dataset_partition_sha256",
        "output_ref",
        "train_ref",
        "train_sha256",
        "train_rows",
        "validation_ref",
        "validation_sha256",
        "validation_rows",
        "total_rows",
    }
)
_CLEANUP_PAYLOAD_FIELDS = frozenset(
    {
        "contract_version",
        "task_id",
        "tenant_scope_digest",
        "catalog_revision",
        "plan_sha256",
        "reason_sha256",
        "artifacts",
    }
)
_CLEANUP_PAYLOAD_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "kind",
        "relative_ref",
        "job_id",
        "attempt_id",
        "sha256",
        "size_bytes",
    }
)
_CLEANUP_RESULT_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "tenant_scope_digest",
        "plan_sha256",
        "status",
        "deleted_count",
        "artifacts",
        "paths_exposed",
        "replayed",
    }
)
_CLEANUP_RESULT_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "kind", "status", "sha256"}
)
_OPAQUE_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UnslothWorkerResultError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HubUnslothWorkerResultProjector:
    """Validate a Worker receipt before mutating Hub read models."""

    def __init__(
        self,
        model_import_results: UnslothModelImportResultHandler,
        storage_catalog: StorageCleanupCompletionPort | None = None,
    ) -> None:
        self._model_import_results = model_import_results
        self._storage_catalog = storage_catalog

    def project(
        self,
        *,
        task_id: str,
        task: Mapping[str, Any],
        response: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        context = task.get("worker_execution_context")
        envelope = (
            context.get("unsloth_task")
            if isinstance(context, Mapping)
            else None
        )
        is_unsloth = isinstance(envelope, Mapping)
        if (
            not is_unsloth
            and response.get("schema")
            != UNSLOTH_WORKER_RESULT_SCHEMA
        ):
            return None
        if not is_unsloth:
            raise UnslothWorkerResultError(
                "unsloth_worker_result_task_context_missing"
            )
        context_task_type = str(
            envelope.get("task_type") or ""
        )
        if context_task_type not in {
            "ml.model.import",
            "ml.dataset.recipe.materialize",
            "ml.storage.cleanup",
            "unsloth.mcp.stop_training",
        }:
            return None
        allowed_response_fields = _RESULT_FIELDS | {
            "handler_contract"
        }
        if (
            response.get("schema")
            != UNSLOTH_WORKER_RESULT_SCHEMA
            or not _RESULT_FIELDS.issubset(response)
            or set(response) - allowed_response_fields
        ):
            raise UnslothWorkerResultError(
                "unsloth_worker_result_contract_invalid"
            )
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise UnslothWorkerResultError(
                "unsloth_worker_result_task_context_invalid"
            )
        try:
            payload_sha256 = unsloth_payload_sha256(
                payload
            )
        except (TypeError, ValueError) as exc:
            raise UnslothWorkerResultError(
                "unsloth_worker_result_task_context_invalid"
            ) from exc
        task_type = context_task_type
        if (
            response.get("task_id") != task_id
            or response.get("task_type") != task_type
            or response.get("tenant_id")
            != envelope.get("tenant_id")
            or response.get("payload_sha256")
            != payload_sha256
            or envelope.get("payload_sha256")
            != payload_sha256
            or envelope.get(
                "followup_task_creation_allowed"
            )
            is not False
        ):
            raise UnslothWorkerResultError(
                "unsloth_worker_result_binding_invalid"
            )
        status = str(response.get("status") or "")
        reason_code = response.get("reason_code")
        result = response.get("result")
        if status == "failed":
            if (
                result is not None
                or re.fullmatch(
                    r"[a-z0-9][a-z0-9_:-]{0,159}",
                    str(reason_code or ""),
                )
                is None
            ):
                raise UnslothWorkerResultError(
                    "unsloth_worker_failure_result_invalid"
                )
            return {
                "unsloth_worker_result": {
                    field: response.get(field)
                    for field in _RESULT_FIELDS
                }
            }
        if (
            status != "completed"
            or reason_code is not None
            or not isinstance(result, Mapping)
        ):
            raise UnslothWorkerResultError(
                "unsloth_worker_result_status_invalid"
            )
        projected = {
            "unsloth_worker_result": {
                field: response.get(field)
                for field in _RESULT_FIELDS
            }
        }
        if task_type == "ml.storage.cleanup":
            if (
                envelope.get("result_handler")
                != "unsloth_storage_cleanup_v1"
                or response.get("handler_contract")
                not in (None, "unsloth_storage_cleanup_v1")
            ):
                raise UnslothWorkerResultError(
                    "unsloth_worker_result_handler_invalid"
                )
            cleanup = self._validate_cleanup_result(
                task_id=task_id,
                payload=payload,
                result=result,
            )
            if self._storage_catalog is None:
                raise UnslothWorkerResultError(
                    "unsloth_storage_catalog_unavailable"
                )
            try:
                self._storage_catalog.mark_cleanup_completed(
                    tenant_id=str(envelope.get("tenant_id") or ""),
                    owner_scope_digest=str(
                        cleanup["tenant_scope_digest"]
                    ),
                    task_id=task_id,
                    artifacts=cleanup["artifacts"],
                )
            except UnslothStorageError as exc:
                raise UnslothWorkerResultError(
                    exc.reason_code
                ) from exc
            projected["unsloth_storage_cleanup"] = cleanup
            return projected
        if task_type == "unsloth.mcp.stop_training":
            if (
                envelope.get("result_handler")
                != "unsloth_mcp_control_v1"
                or set(result)
                != {
                    "schema",
                    "tool_id",
                    "correlation_id",
                    "accepted",
                }
                or result.get("schema")
                != "ananta.unsloth-mcp-control-result.v1"
                or result.get("tool_id") != "stop_training"
                or result.get("accepted") is not True
                or result.get("correlation_id")
                != payload.get("correlation_id")
            ):
                raise UnslothWorkerResultError(
                    "unsloth_mcp_control_result_binding_invalid"
                )
            projected["unsloth_mcp_control"] = dict(result)
            return projected
        if task_type == "ml.model.import":
            if (
                envelope.get("result_handler")
                != "unsloth_model_import_v1"
            ):
                raise UnslothWorkerResultError(
                    "unsloth_worker_result_handler_invalid"
                )
            imported, outbox = (
                self._model_import_results
                .handle_with_completion_outbox(
                    task_id=task_id,
                    task_payload=dict(payload),
                    worker_result=dict(result),
                    worker_envelope=dict(
                        projected[
                            "unsloth_worker_result"
                        ]
                    ),
                )
            )
            projected["unsloth_model_import"] = (
                imported.model_dump(
                    mode="json",
                    by_alias=True,
                )
            )
            projected[
                "_unsloth_completion_outbox_task_id"
            ] = outbox.task_id
            return projected
        if task_type == "ml.dataset.recipe.materialize":
            if (
                envelope.get("result_handler")
                != "unsloth_data_recipe_v1"
            ):
                raise UnslothWorkerResultError(
                    "unsloth_worker_result_handler_invalid"
                )
            projected["unsloth_data_recipe"] = (
                self._validate_recipe_result(
                    task_id=task_id,
                    manifest=dict(
                        payload.get("manifest") or {}
                    ),
                    result=dict(result),
                )
            )
            return projected
        raise UnslothWorkerResultError(
            "unsloth_worker_task_type_invalid"
        )

    @staticmethod
    def _validate_cleanup_result(
        *,
        task_id: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload_artifacts = payload.get("artifacts")
        if (
            set(payload) != _CLEANUP_PAYLOAD_FIELDS
            or payload.get("contract_version")
            != "ananta.unsloth-storage-cleanup-task.v1"
            or payload.get("task_id") != task_id
            or _SHA256.fullmatch(
                str(payload.get("tenant_scope_digest") or "")
            )
            is None
            or _SHA256.fullmatch(
                str(payload.get("plan_sha256") or "")
            )
            is None
            or _SHA256.fullmatch(
                str(payload.get("reason_sha256") or "")
            )
            is None
            or isinstance(payload.get("catalog_revision"), bool)
            or not isinstance(payload.get("catalog_revision"), int)
            or int(payload["catalog_revision"]) < 0
            or not isinstance(payload_artifacts, list)
            or not 1 <= len(payload_artifacts) <= 128
        ):
            raise UnslothWorkerResultError(
                "unsloth_storage_cleanup_task_context_invalid"
            )
        expected: dict[str, tuple[str, str]] = {}
        for item in payload_artifacts:
            if (
                not isinstance(item, Mapping)
                or set(item) != _CLEANUP_PAYLOAD_ARTIFACT_FIELDS
                or _OPAQUE_ID.fullmatch(
                    str(item.get("artifact_id") or "")
                )
                is None
                or str(item.get("kind") or "")
                not in {"workspace", "checkpoint", "export"}
                or _OPAQUE_ID.fullmatch(
                    str(item.get("job_id") or "")
                )
                is None
                or _OPAQUE_ID.fullmatch(
                    str(item.get("attempt_id") or "")
                )
                is None
                or _SHA256.fullmatch(
                    str(item.get("sha256") or "")
                )
                is None
                or isinstance(item.get("size_bytes"), bool)
                or not isinstance(item.get("size_bytes"), int)
                or int(item["size_bytes"]) < 0
                or not _contained_relative_ref(
                    str(item.get("relative_ref") or "")
                )
            ):
                raise UnslothWorkerResultError(
                    "unsloth_storage_cleanup_task_context_invalid"
                )
            artifact_id = str(item["artifact_id"])
            if artifact_id in expected:
                raise UnslothWorkerResultError(
                    "unsloth_storage_cleanup_task_context_invalid"
                )
            expected[artifact_id] = (
                str(item["kind"]),
                str(item["sha256"]),
            )
        result_artifacts = result.get("artifacts")
        if (
            set(result) != _CLEANUP_RESULT_FIELDS
            or result.get("schema")
            != "ananta.unsloth-storage-cleanup-result.v1"
            or result.get("task_id") != task_id
            or result.get("tenant_scope_digest")
            != payload.get("tenant_scope_digest")
            or result.get("plan_sha256")
            != payload.get("plan_sha256")
            or result.get("status") != "completed"
            or result.get("paths_exposed") is not False
            or not isinstance(result.get("replayed"), bool)
            or isinstance(result.get("deleted_count"), bool)
            or not isinstance(result.get("deleted_count"), int)
            or not isinstance(result_artifacts, list)
            or len(result_artifacts) != len(expected)
        ):
            raise UnslothWorkerResultError(
                "unsloth_storage_cleanup_result_binding_invalid"
            )
        observed: set[str] = set()
        deleted_count = 0
        for item in result_artifacts:
            if (
                not isinstance(item, Mapping)
                or set(item) != _CLEANUP_RESULT_ARTIFACT_FIELDS
            ):
                raise UnslothWorkerResultError(
                    "unsloth_storage_cleanup_result_binding_invalid"
                )
            artifact_id = str(item.get("artifact_id") or "")
            expected_binding = expected.get(artifact_id)
            if (
                artifact_id in observed
                or expected_binding is None
                or (
                    str(item.get("kind") or ""),
                    str(item.get("sha256") or ""),
                )
                != expected_binding
                or item.get("status")
                not in {"deleted", "already_absent"}
            ):
                raise UnslothWorkerResultError(
                    "unsloth_storage_cleanup_result_binding_invalid"
                )
            observed.add(artifact_id)
            deleted_count += item["status"] == "deleted"
        if (
            observed != set(expected)
            or result["deleted_count"] != deleted_count
        ):
            raise UnslothWorkerResultError(
                "unsloth_storage_cleanup_result_binding_invalid"
            )
        return dict(result)

    @staticmethod
    def _validate_recipe_result(
        *,
        task_id: str,
        manifest: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (
            set(result) != _RECIPE_RESULT_FIELDS
            or result.get("schema")
            != "ananta.unsloth-data-recipe-result.v1"
            or result.get("recipe_id")
            != manifest.get("recipe_id")
            or result.get("attempt_id") != task_id
            or result.get("dataset_hash")
            != manifest.get("dataset_hash")
            or result.get("dataset_partition_sha256")
            != manifest.get("dataset_partition_sha256")
        ):
            raise UnslothWorkerResultError(
                "unsloth_data_recipe_result_binding_invalid"
            )
        row_fields = (
            "train_rows",
            "validation_rows",
            "total_rows",
        )
        if any(
            isinstance(result.get(field), bool)
            or not isinstance(result.get(field), int)
            or int(result.get(field)) < 0
            for field in row_fields
        ) or (
            int(result["train_rows"])
            + int(result["validation_rows"])
            != int(result["total_rows"])
        ) or int(result["total_rows"]) != int(
            manifest.get("row_count") or -1
        ):
            raise UnslothWorkerResultError(
                "unsloth_data_recipe_result_rows_invalid"
            )
        for field in (
            "dataset_hash",
            "dataset_partition_sha256",
            "train_sha256",
            "validation_sha256",
        ):
            value = str(result.get(field) or "")
            if re.fullmatch(
                r"[0-9a-f]{64}",
                value,
            ) is None:
                raise UnslothWorkerResultError(
                    "unsloth_data_recipe_result_hash_invalid"
                )
        recipe_id = str(result["recipe_id"])
        if str(result.get("output_ref") or "") != recipe_id:
            raise UnslothWorkerResultError(
                "unsloth_data_recipe_result_reference_invalid"
            )
        for field in (
            "output_ref",
            "train_ref",
            "validation_ref",
        ):
            value = str(result.get(field) or "")
            path = PurePosixPath(value)
            if (
                not value
                or path.is_absolute()
                or ".." in path.parts
                or value != path.as_posix()
            ):
                raise UnslothWorkerResultError(
                    (
                        "unsloth_data_recipe_result_"
                        "reference_invalid"
                    )
                )
        if (
            not str(result["train_ref"]).startswith(
                f"{recipe_id}/"
            )
            or not str(
                result["validation_ref"]
            ).startswith(f"{recipe_id}/")
        ):
            raise UnslothWorkerResultError(
                "unsloth_data_recipe_result_reference_invalid"
            )
        return dict(result)


def get_unsloth_worker_result_projector(
) -> HubUnslothWorkerResultProjector:
    if not has_app_context():
        raise RuntimeError(
            "unsloth_worker_result_app_context_required"
        )
    configured = current_app.extensions.get(
        "unsloth_worker_result_projector"
    )
    if isinstance(
        configured,
        HubUnslothWorkerResultProjector,
    ):
        return configured
    from agent.services.unsloth_completion_outbox_service import (
        get_unsloth_completion_outbox_reconciler,
    )

    get_unsloth_completion_outbox_reconciler(
    ).reconcile_pending(limit=32)
    projector = HubUnslothWorkerResultProjector(
        UnslothModelImportResultHandler(
            get_unsloth_model_catalog_registry()
        ),
        storage_catalog=_storage_catalog(),
    )
    current_app.extensions[
        "unsloth_worker_result_projector"
    ] = projector
    return projector


def _storage_catalog() -> StorageCleanupCompletionPort:
    raw_agent = dict(
        current_app.config.get("AGENT_CONFIG", {}) or {}
    )
    training = dict(
        raw_agent.get("ml_intern_training") or {}
    )
    artifact_root = str(
        os.getenv("ANANTA_LORA_TRAINING_ARTIFACT_ROOT", "")
    ).strip()
    if artifact_root:
        training["artifact_root"] = artifact_root
    return storage_catalog_from_config(training)


def _contained_relative_ref(value: str) -> bool:
    pure = PurePosixPath(value)
    return bool(
        value
        and "\x00" not in value
        and "\\" not in value
        and not pure.is_absolute()
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )


__all__ = [
    "HubUnslothWorkerResultProjector",
    "UNSLOTH_WORKER_RESULT_SCHEMA",
    "UnslothWorkerResultError",
    "get_unsloth_worker_result_projector",
]
