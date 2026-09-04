"""Worker-only handlers for Hub-delegated Unsloth platform tasks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.unsloth_task import (
    UNSLOTH_STORAGE_CLEANUP_CAPABILITY,
    UNSLOTH_STORAGE_CLEANUP_TASK_TYPE,
    build_unsloth_task_result,
    unsloth_payload_sha256,
)
from worker.training.model_imports import (
    HuggingFaceSnapshotDownloadAdapter,
    ImmutableModelImportExecutor,
    ModelImportCommand,
)
from worker.training.storage_cleanup import (
    WorkerStorageCleanupExecutor,
)

UNSLOTH_WORKER_RESULT_SCHEMA = "ananta.unsloth-worker-task-result.v1"


class DatasetRecipeMaterializationPort(Protocol):
    def materialize(
        self,
        manifest: Mapping[str, Any],
        *,
        attempt_id: str,
    ) -> Mapping[str, Any]: ...


class McpStopTrainingPort(Protocol):
    def stop_training(
        self,
        *,
        save: bool,
        correlation_id: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class _TaskBinding:
    task_id: str
    task_type: str
    tenant_id: str
    payload_sha256: str
    payload: dict[str, Any]


class UnslothModelImportTaskHandler:
    task_type = "ml.model.import"
    _FIELDS = frozenset(
        {
            "schema_version",
            "tenant_id",
            "project_id",
            "source_id",
            "kind",
            "expected_sha256",
            "artifact_id",
            "model_id",
            "revision",
            "max_bytes",
            "allow_patterns",
            "trust_remote_code",
            "network_authorized",
            "license_status",
            "format",
            "architecture",
            "quantization",
            "capability_facets",
        }
    )

    def __init__(self, executor: ImmutableModelImportExecutor) -> None:
        self._executor = executor

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        return _proposal(
            binding,
            capability="unsloth_model_import",
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        try:
            result = self.handle(binding.payload)
        except Exception as exc:  # noqa: BLE001 - closed worker boundary
            return _result_envelope(
                binding,
                status="failed",
                reason_code=_reason(exc),
            )
        return _result_envelope(
            binding,
            status="completed",
            result=result,
        )

    def handle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if set(payload) != self._FIELDS or payload.get("schema_version") != 2:
            raise ValueError("model_import_task_contract_invalid")
        allow_patterns = payload.get("allow_patterns")
        facets = payload.get("capability_facets")
        if (
            not isinstance(allow_patterns, list)
            or not isinstance(facets, list)
            or any(not isinstance(item, str) for item in (*allow_patterns, *facets))
        ):
            raise ValueError("model_import_task_contract_invalid")
        max_bytes = payload.get("max_bytes")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("model_import_task_contract_invalid")
        required_strings = (
            "tenant_id",
            "project_id",
            "source_id",
            "kind",
            "expected_sha256",
            "license_status",
            "format",
            "architecture",
        )
        optional_strings = ("artifact_id", "model_id", "revision", "quantization")
        if (
            any(not isinstance(payload.get(field), str) for field in required_strings)
            or any(
                payload.get(field) is not None and not isinstance(payload.get(field), str)
                for field in optional_strings
            )
            or not isinstance(payload.get("trust_remote_code"), bool)
            or not isinstance(payload.get("network_authorized"), bool)
        ):
            raise ValueError("model_import_task_contract_invalid")
        result = self._executor.execute(
            ModelImportCommand(
                tenant_id=str(payload.get("tenant_id") or ""),
                project_id=str(payload.get("project_id") or ""),
                source_id=str(payload.get("source_id") or ""),
                kind=str(payload.get("kind") or ""),
                expected_sha256=str(payload.get("expected_sha256") or ""),
                artifact_id=payload.get("artifact_id"),
                model_id=payload.get("model_id"),
                revision=payload.get("revision"),
                max_bytes=max_bytes,
                allow_patterns=tuple(allow_patterns),
                trust_remote_code=payload["trust_remote_code"],
                network_authorized=payload["network_authorized"],
                license_status=str(payload.get("license_status") or ""),
                model_format=str(payload.get("format") or ""),
                architecture=str(payload.get("architecture") or ""),
                quantization=payload.get("quantization"),
                capability_facets=tuple(facets),
            )
        )
        return {
            "schema": "ananta.unsloth-model-import-result.v1",
            "cache_key": result.cache_key,
            "relative_path": result.relative_path,
            "content_sha256": result.content_sha256,
            "file_count": result.file_count,
            "total_bytes": result.total_bytes,
        }


class UnslothDataRecipeTaskHandler:
    task_type = "ml.dataset.recipe.materialize"

    def __init__(self, materializer: DatasetRecipeMaterializationPort) -> None:
        self._materializer = materializer

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        return _proposal(
            binding,
            capability="unsloth_dataset_materialization",
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        try:
            result = self.handle(
                binding.payload,
                attempt_id=binding.task_id,
            )
        except Exception as exc:  # noqa: BLE001 - closed worker boundary
            return _result_envelope(
                binding,
                status="failed",
                reason_code=_reason(exc),
            )
        return _result_envelope(
            binding,
            status="completed",
            result=result,
        )

    def handle(
        self,
        payload: Mapping[str, Any],
        *,
        attempt_id: str,
    ) -> Mapping[str, Any]:
        if set(payload) != {"schema", "manifest"} or payload.get("schema") != (
            "ananta.unsloth-data-recipe-task.v1"
        ):
            raise ValueError("data_recipe_task_contract_invalid")
        manifest = payload.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("data_recipe_task_contract_invalid")
        return self._materializer.materialize(
            dict(manifest),
            attempt_id=attempt_id,
        )


class UnslothStorageCleanupTaskHandler:
    task_type = UNSLOTH_STORAGE_CLEANUP_TASK_TYPE

    def __init__(
        self,
        executor: WorkerStorageCleanupExecutor,
    ) -> None:
        self._executor = executor

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        return _proposal(
            binding,
            capability=UNSLOTH_STORAGE_CLEANUP_CAPABILITY,
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        try:
            result = self.handle(binding.payload)
            if result.get("task_id") != binding.task_id:
                raise ValueError(
                    "cleanup_task_binding_mismatch"
                )
        except Exception as exc:  # noqa: BLE001 - closed worker boundary
            return _result_envelope(
                binding,
                status="failed",
                reason_code=_reason(exc),
            )
        return _result_envelope(
            binding,
            status="completed",
            result=result,
        )

    def handle(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._executor.execute(payload)


class UnslothMcpStopTrainingTaskHandler:
    task_type = "unsloth.mcp.stop_training"

    def __init__(self, client: McpStopTrainingPort) -> None:
        self._client = client

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        return _proposal(
            binding,
            capability="unsloth_mcp_control",
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        binding = _resolve_task_binding(
            kwargs,
            expected_task_type=self.task_type,
        )
        try:
            result = self.handle(binding.payload)
        except Exception as exc:  # noqa: BLE001 - closed worker boundary
            return _result_envelope(
                binding,
                status="failed",
                reason_code=_reason(exc),
            )
        return _result_envelope(
            binding,
            status="completed",
            result=result,
        )

    def handle(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if (
            set(payload)
            != {
                "schema",
                "actor_id",
                "tool_id",
                "arguments",
                "confirmation_id",
                "replay_nonce",
                "correlation_id",
            }
            or payload.get("schema")
            != "ananta.unsloth_hub_task_command.v1"
            or payload.get("tool_id") != "stop_training"
        ):
            raise ValueError(
                "unsloth_mcp_control_task_contract_invalid"
            )
        arguments = payload.get("arguments")
        if not isinstance(arguments, Mapping) or (
            set(arguments) - {"save"}
        ):
            raise ValueError(
                "unsloth_mcp_control_task_contract_invalid"
            )
        save = arguments.get("save", True)
        if not isinstance(save, bool):
            raise ValueError(
                "unsloth_mcp_control_task_contract_invalid"
            )
        return self._client.stop_training(
            save=save,
            correlation_id=str(
                payload.get("correlation_id") or ""
            ),
        )


def build_unsloth_model_import_task_handler(
    *,
    cache_root: Path,
    artifact_root: Path,
    network_enabled: bool,
) -> UnslothModelImportTaskHandler:
    return UnslothModelImportTaskHandler(
        ImmutableModelImportExecutor(
            cache_root=cache_root,
            artifact_root=artifact_root,
            downloads=HuggingFaceSnapshotDownloadAdapter(),
            network_enabled=network_enabled,
        )
    )


def _resolve_task_binding(
    kwargs: Mapping[str, Any],
    *,
    expected_task_type: str,
) -> _TaskBinding:
    task = kwargs.get("task")
    if not isinstance(task, Mapping):
        raise ValueError("unsloth_worker_task_missing")
    task_id = str(task.get("id") or "")
    context = task.get("worker_execution_context")
    if not isinstance(context, Mapping) or context.get("schema") != (
        "ananta.unsloth-worker-task-context.v1"
    ):
        raise ValueError("unsloth_worker_context_invalid")
    envelope = context.get("unsloth_task")
    expected_fields = {
        "task_type",
        "tenant_id",
        "payload",
        "payload_sha256",
        "result_handler",
        "followup_task_creation_allowed",
    }
    if not isinstance(envelope, Mapping) or set(envelope) != expected_fields:
        raise ValueError("unsloth_worker_context_invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("unsloth_worker_payload_invalid")
    try:
        payload_sha256 = unsloth_payload_sha256(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "unsloth_worker_payload_invalid"
        ) from exc
    task_type = str(envelope.get("task_type") or "")
    expected_handler = {
        "ml.model.import": "unsloth_model_import_v1",
        "ml.dataset.recipe.materialize": (
            "unsloth_data_recipe_v1"
        ),
        "unsloth.mcp.stop_training": (
            "unsloth_mcp_control_v1"
        ),
    }.get(expected_task_type)
    if (
        not task_id.startswith("unsloth-")
        or task_type != expected_task_type
        or str(envelope.get("payload_sha256") or "") != payload_sha256
        or str(envelope.get("result_handler") or "") != expected_handler
        or envelope.get("followup_task_creation_allowed") is not False
    ):
        raise ValueError("unsloth_worker_binding_invalid")
    return _TaskBinding(
        task_id=task_id,
        task_type=task_type,
        tenant_id=str(envelope.get("tenant_id") or ""),
        payload_sha256=payload_sha256,
        payload=dict(payload),
    )


def _proposal(
    binding: _TaskBinding,
    *,
    capability: str,
) -> dict[str, Any]:
    return {
        "proposal_id": f"{binding.task_id}-proposal",
        "strategy_id": "deterministic_handler",
        "command": None,
        "tool_calls": [
            {
                "name": binding.task_type,
                "arguments": {
                    "task_id": binding.task_id,
                    "payload_sha256": binding.payload_sha256,
                },
            }
        ],
        "expected_artifacts": [
            {
                "kind": "unsloth_worker_result",
                "required": True,
                "schema": UNSLOTH_WORKER_RESULT_SCHEMA,
            }
        ],
        "safety_flags": {
            "worker_only": True,
            "hub_delegation_required": True,
            "worker_orchestration_forbidden": True,
            "followup_task_creation_forbidden": True,
            "required_capability": capability,
        },
    }


def _result_envelope(
    binding: _TaskBinding,
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return build_unsloth_task_result(
        task_id=binding.task_id,
        task_type=binding.task_type,
        tenant_id=binding.tenant_id,
        payload_sha256=binding.payload_sha256,
        status=status,
        reason_code=reason_code,
        result=result,
    )


def _reason(exc: BaseException) -> str:
    value = str(
        getattr(exc, "reason_code", "")
        or getattr(exc, "code", "")
        or ""
    ).strip()
    if value and len(value) <= 160 and all(
        character in "abcdefghijklmnopqrstuvwxyz0123456789_:-"
        for character in value
    ):
        return value
    return (
        "unsloth_worker_execution_failed:"
        f"{type(exc).__name__.lower()}"
    )


__all__ = [
    "DatasetRecipeMaterializationPort",
    "McpStopTrainingPort",
    "UNSLOTH_WORKER_RESULT_SCHEMA",
    "UnslothDataRecipeTaskHandler",
    "UnslothModelImportTaskHandler",
    "UnslothMcpStopTrainingTaskHandler",
    "build_unsloth_model_import_task_handler",
]
