"""Profile-gated composition for Hub-dispatched Unsloth worker handlers."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from worker.training.data_recipe_materializer import (
    DataRecipeMaterializationError,
    FilesystemDatasetRecipeMaterializer,
)
from worker.training.unsloth_task_handlers import (
    UnslothDataRecipeTaskHandler,
    UnslothMcpStopTrainingTaskHandler,
    UnslothStorageCleanupTaskHandler,
    build_unsloth_model_import_task_handler,
)
from worker.training.storage_cleanup import (
    WorkerStorageCleanupExecutor,
)
from worker.training.unsloth_mcp_control import (
    UnslothMcpStopTrainingClient,
)

MODEL_IMPORT_MODE = "model_import_network"
DATA_RECIPE_MODE = "data_recipe"
MCP_CONTROL_MODE = "studio_mcp_control"
STORAGE_CLEANUP_MODE = "storage_cleanup"


@dataclass(frozen=True)
class UnslothWorkerHandlerBinding:
    task_kind: str
    handler: Any
    capabilities: tuple[str, ...]
    safety_flags: dict[str, Any]
    verification_hooks: tuple[str, ...]


@dataclass(frozen=True)
class UnslothWorkerRuntime:
    profile: str
    status: str
    reason_code: str | None
    network_access: str
    bindings: tuple[UnslothWorkerHandlerBinding, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def health_snapshot(self) -> dict[str, Any]:
        capabilities = sorted(
            {
                capability
                for binding in self.bindings
                for capability in binding.capabilities
            }
        )
        return {
            "status": self.status,
            "ready": self.ready,
            "profile": self.profile or None,
            "reason_code": self.reason_code,
            "network_access": self.network_access,
            "capabilities": capabilities,
            "task_kinds": [
                binding.task_kind
                for binding in self.bindings
            ],
        }


def build_unsloth_worker_runtime(
    env: Mapping[str, str] | None = None,
) -> UnslothWorkerRuntime:
    source = os.environ if env is None else env
    profile = str(
        source.get("ANANTA_UNSLOTH_WORKER_MODE") or ""
    ).strip()
    if not profile:
        return UnslothWorkerRuntime(
            profile="",
            status="disabled",
            reason_code="unsloth_worker_profile_not_selected",
            network_access="none",
        )
    try:
        if profile == MODEL_IMPORT_MODE:
            if not _truthy(
                source.get(
                    "ANANTA_UNSLOTH_MODEL_NETWORK_ENABLED"
                )
            ):
                raise ValueError(
                    "unsloth_model_import_network_opt_in_required"
                )
            handler = build_unsloth_model_import_task_handler(
                cache_root=_directory(
                    source,
                    "ANANTA_UNSLOTH_MODEL_CACHE_ROOT",
                ),
                artifact_root=_directory(
                    source,
                    "ANANTA_UNSLOTH_MODEL_ARTIFACT_ROOT",
                ),
                network_enabled=True,
            )
            binding = UnslothWorkerHandlerBinding(
                task_kind="ml.model.import",
                handler=handler,
                capabilities=("unsloth_model_import",),
                safety_flags={
                    "worker_only": True,
                    "hub_delegation_required": True,
                    "worker_orchestration_forbidden": True,
                    "network_access": (
                        "huggingface_snapshot_only"
                    ),
                    "network_opt_in_required": True,
                },
                verification_hooks=(
                    "unsloth_worker_result_v1",
                    "model_snapshot_sha256",
                    "license_approval",
                ),
            )
            return UnslothWorkerRuntime(
                profile=profile,
                status="ready",
                reason_code=None,
                network_access="huggingface_snapshot_only",
                bindings=(binding,),
            )
        if profile == DATA_RECIPE_MODE:
            if not all(
                _truthy(source.get(name))
                for name in (
                    "HF_DATASETS_OFFLINE",
                    "HF_HUB_OFFLINE",
                    "TRANSFORMERS_OFFLINE",
                )
            ):
                raise ValueError(
                    "unsloth_data_recipe_offline_mode_required"
                )
            handler = UnslothDataRecipeTaskHandler(
                FilesystemDatasetRecipeMaterializer(
                    dataset_root=_directory(
                        source,
                        "ANANTA_UNSLOTH_RECIPE_DATASET_ROOT",
                    ),
                    attempt_output_root=_directory(
                        source,
                        (
                            "ANANTA_UNSLOTH_RECIPE_"
                            "ATTEMPT_OUTPUT_ROOT"
                        ),
                    ),
                    max_dataset_bytes=_bounded_int(
                        source,
                        (
                            "ANANTA_UNSLOTH_RECIPE_"
                            "MAX_DATASET_BYTES"
                        ),
                        default=4 * 1024**3,
                        maximum=100 * 1024**3,
                    ),
                    max_output_bytes=_bounded_int(
                        source,
                        (
                            "ANANTA_UNSLOTH_RECIPE_"
                            "MAX_OUTPUT_BYTES"
                        ),
                        default=8 * 1024**3,
                        maximum=200 * 1024**3,
                    ),
                    max_records=_bounded_int(
                        source,
                        "ANANTA_UNSLOTH_RECIPE_MAX_RECORDS",
                        default=10_000_000,
                        maximum=10_000_000,
                    ),
                )
            )
            binding = UnslothWorkerHandlerBinding(
                task_kind="ml.dataset.recipe.materialize",
                handler=handler,
                capabilities=(
                    "unsloth_dataset_materialization",
                ),
                safety_flags={
                    "worker_only": True,
                    "hub_delegation_required": True,
                    "worker_orchestration_forbidden": True,
                    "network_access": "none",
                    "dataset_mount": "read_only",
                    "output_mount": "attempt_scoped",
                },
                verification_hooks=(
                    "unsloth_worker_result_v1",
                    "dataset_partition_sha256",
                    "attempt_output_binding",
                ),
            )
            return UnslothWorkerRuntime(
                profile=profile,
                status="ready",
                reason_code=None,
                network_access="none",
                bindings=(binding,),
            )
        if profile == STORAGE_CLEANUP_MODE:
            handler = UnslothStorageCleanupTaskHandler(
                WorkerStorageCleanupExecutor(
                    state_root=_directory(
                        source,
                        "ANANTA_UNSLOTH_STORAGE_CLEANUP_STATE_ROOT",
                    ),
                    workspace_root=_directory(
                        source,
                        (
                            "ANANTA_UNSLOTH_STORAGE_CLEANUP_"
                            "WORKSPACE_ROOT"
                        ),
                    ),
                )
            )
            binding = UnslothWorkerHandlerBinding(
                task_kind="ml.storage.cleanup",
                handler=handler,
                capabilities=("unsloth_storage_cleanup",),
                safety_flags={
                    "worker_only": True,
                    "hub_delegation_required": True,
                    "worker_orchestration_forbidden": True,
                    "network_access": "none",
                    "tenant_attempt_binding_required": True,
                    "paths_exposed": False,
                },
                verification_hooks=(
                    "unsloth_worker_result_v1",
                    "unsloth_storage_cleanup_v1",
                    "cleanup_payload_sha256",
                ),
            )
            return UnslothWorkerRuntime(
                profile=profile,
                status="ready",
                reason_code=None,
                network_access="none",
                bindings=(binding,),
            )
        if profile == MCP_CONTROL_MODE:
            handler = UnslothMcpStopTrainingTaskHandler(
                UnslothMcpStopTrainingClient.from_environment(
                    source
                )
            )
            binding = UnslothWorkerHandlerBinding(
                task_kind="unsloth.mcp.stop_training",
                handler=handler,
                capabilities=("unsloth_mcp_control",),
                safety_flags={
                    "worker_only": True,
                    "hub_delegation_required": True,
                    "worker_orchestration_forbidden": True,
                    "network_access": "studio_internal_only",
                    "allowed_tool": "stop_training",
                },
                verification_hooks=(
                    "unsloth_worker_result_v1",
                    "unsloth_mcp_control_v1",
                    "correlation_binding",
                ),
            )
            return UnslothWorkerRuntime(
                profile=profile,
                status="ready",
                reason_code=None,
                network_access="studio_internal_only",
                bindings=(binding,),
            )
        raise ValueError("unsloth_worker_profile_invalid")
    except (
        DataRecipeMaterializationError,
        OSError,
        ValueError,
    ) as exc:
        reason = str(exc).strip()
        return UnslothWorkerRuntime(
            profile=profile,
            status="error",
            reason_code=(
                reason
                or "unsloth_worker_configuration_invalid"
            ),
            network_access=(
                "huggingface_snapshot_only"
                if profile == MODEL_IMPORT_MODE
                else (
                    "studio_internal_only"
                    if profile == MCP_CONTROL_MODE
                    else "none"
                )
            ),
        )


def _directory(
    source: Mapping[str, str],
    name: str,
) -> Path:
    raw = str(source.get(name) or "").strip()
    path = Path(raw)
    if (
        not raw
        or not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise ValueError(f"{name.lower()}_invalid")
    return path


def _bounded_int(
    source: Mapping[str, str],
    name: str,
    *,
    default: int,
    maximum: int,
) -> int:
    raw = str(source.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError as exc:
        raise ValueError(
            f"{name.lower()}_invalid"
        ) from exc
    if not 0 < value <= maximum:
        raise ValueError(f"{name.lower()}_invalid")
    return value


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = [
    "DATA_RECIPE_MODE",
    "MODEL_IMPORT_MODE",
    "MCP_CONTROL_MODE",
    "STORAGE_CLEANUP_MODE",
    "UnslothWorkerHandlerBinding",
    "UnslothWorkerRuntime",
    "build_unsloth_worker_runtime",
]
