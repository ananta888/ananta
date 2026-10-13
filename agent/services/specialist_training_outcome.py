"""Project technical training outcomes from Hub jobs and verified registry bytes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, ContextManager

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService, make_config_hash
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_training_artifact_binding import MlInternTrainingArtifactBinding
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_benchmark import ResourceUsage
from agent.services.specialist_dataset_binding import BINDING_KEY, require_specialist_dataset_binding
from agent.services.specialist_job_predictions import SpecialistJobReadPort
from agent.services.specialist_model_factory import TrainingOutcome
from agent.services.unsloth_storage_governance_service import tenant_scope_digest


def _manifest_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("specialist_training_manifest_duplicate_field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("specialist_training_manifest_nonfinite_json")


class SpecialistTrainingOutcomeService:
    """Read an existing completed result; no execution, registration or promotion."""

    def __init__(
        self,
        *,
        repository: SpecialistJobReadPort,
        registry: MlInternAdapterRegistryService,
        artifact_root: Path,
        model_catalog: Mapping[str, Mapping[str, Any]],
        open_artifact: Callable[[str], ContextManager[BinaryIO]],
    ):
        self._repository = repository
        self._registry = registry
        self._security = MlInternArtifactSecurityService(storage_root=artifact_root)
        self._models = {name: dict(value) for name, value in model_catalog.items()}
        self._open_artifact = open_artifact

    def read(
        self, principal: MlInternTrainingPrincipal, job_id: str, command: CreateTrainingJobCommand
    ) -> TrainingOutcome:
        job = self._repository.get_job(principal, job_id)
        if (
            job is None
            or job.tenant_id != principal.tenant_id
            or job.owner_subject != principal.subject
            or job.status != "completed"
            or job.job_type != "train_lora"
            or job.request_spec.get("task_family") != "specialist_decision"
            or any(job.request_spec.get(key) != value for key, value in command.request_spec.items())
        ):
            raise ValueError("specialist_training_job_binding_invalid")
        attempt = self._repository.get_attempt(str(job.active_attempt_id or ""))
        if (
            attempt is None
            or attempt.id != job.active_attempt_id
            or attempt.job_id != job.id
            or attempt.tenant_id != principal.tenant_id
            or attempt.owner_subject != principal.subject
            or attempt.status != "completed"
        ):
            raise ValueError("specialist_training_attempt_invalid")
        dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
        if dataset is None or dataset.tenant_id != principal.tenant_id or dataset.owner_subject != principal.subject:
            raise ValueError("specialist_training_dataset_invalid")
        require_specialist_dataset_binding(job.request_spec, dataset)
        adapter_id = f"adapter-{job.id.removeprefix('lora-job-')}"
        record = self._registry.get(adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
        if (
            record is None
            or job.adapter_id != adapter_id
            or record.status not in {"trained", "evaluated", "approved"}
            or record.base_model != job.base_model
            or record.method != job.request_spec.get("method")
            or record.config_hash != make_config_hash(job.request_spec)
            or record.dataset_hash != job.request_spec.get("dataset_hash")
        ):
            raise ValueError("specialist_training_registry_binding_invalid")
        storage = MlInternTrainingArtifactBinding(tenant_scope_digest(principal), job.id, attempt.id)
        relative = storage.relative_directory("adapter")
        path = self._security.resolve_relative(relative, must_exist=True)
        if record.artifact_paths.get("adapter_storage_ref") != relative or record.artifact_paths.get(
            "adapter_dir"
        ) != str(path):
            raise ValueError("specialist_training_artifact_scope_invalid")
        inspected = self._security.validate_adapter_tree(path)
        if inspected["tree_sha256"] != record.artifact_sha256:
            raise ValueError("specialist_training_artifact_hash_mismatch")
        model = self._models.get(str(job.base_model)) or {}
        digest = model.get("snapshot_hash")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("specialist_training_base_model_unverified")
        artifacts = (job.result_summary or {}).get("artifacts") or []
        manifests = [
            item for item in artifacts if isinstance(item, dict) and item.get("name") == "training_manifest.json"
        ]
        if len(manifests) != 1:
            raise ValueError("specialist_training_manifest_unavailable")
        with self._open_artifact(storage.relative_directory("artifacts") + "/training_manifest.json") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if (
            len(raw) > 4 * 1024 * 1024
            or len(raw) != manifests[0].get("size_bytes")
            or hashlib.sha256(raw).hexdigest() != manifests[0].get("sha256")
        ):
            raise ValueError("specialist_training_manifest_hash_mismatch")
        try:
            manifest = json.loads(raw, object_pairs_hook=_manifest_fields, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("specialist_training_manifest_json_invalid") from exc
        model_binding = manifest.get("base_model") if isinstance(manifest, dict) else None
        token = manifest.get("fencing_token") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("job_id") != job.id
            or manifest.get("schema_version") != "ananta.lora-training-manifest.v1"
            or manifest.get("contract_version") != "ananta.lora-training.v1"
            or manifest.get("backend") != job.backend
            or manifest.get("attempt_id") != attempt.id
            or type(token) is not int
            or hashlib.sha256(str(token).encode()).hexdigest() != attempt.fencing_token_digest
            or not isinstance(model_binding, dict)
            or model_binding.get("model_id") != job.base_model
            or model_binding.get("snapshot_hash") != digest
        ):
            raise ValueError("specialist_training_manifest_binding_invalid")
        partitions = manifest.get("dataset")
        pinned = job.request_spec[BINDING_KEY]
        if (
            not isinstance(partitions, dict)
            or partitions.get("dataset_id") != dataset.id
            or any(
                not isinstance(partitions.get(name), dict)
                or partitions[name].get("sha256") != pinned[f"{name}_sha256"]
                or type(partitions[name].get("record_count")) is not int
                or partitions[name]["record_count"] != pinned[f"{name}_records"]
                for name in ("train", "validation")
            )
        ):
            raise ValueError("specialist_training_manifest_dataset_invalid")
        usage = ResourceUsage(adapter_bytes=inspected["total_bytes"])
        current = self._repository.get_job(principal, job_id)
        current_attempt = self._repository.get_attempt(attempt.id)
        current_dataset = self._repository.get_dataset(principal, dataset.id)
        if (
            current is None
            or current.version != job.version
            or current.status != "completed"
            or current.active_attempt_id != attempt.id
            or current_attempt is None
            or current_attempt.version != attempt.version
            or current_attempt.status != "completed"
            or current_dataset is None
        ):
            raise ValueError("specialist_training_job_changed")
        require_specialist_dataset_binding(job.request_spec, current_dataset)
        return TrainingOutcome(job.id, record.artifact_sha256, digest, usage, adapter_id)
