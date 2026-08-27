"""Publish verified worker outcomes into the adapter lifecycle registry."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent.db_models import MlInternTrainingJobDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    get_ml_intern_training_repository,
)
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    make_config_hash,
)
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_evaluation_decision_service import evaluate_adapter_metrics
from agent.services.ml_intern_evaluation_store_service import MlInternEvaluationStoreService
from agent.services.ml_intern_training_artifact_binding import (
    MlInternTrainingArtifactBinding,
)
from agent.services.ml_intern_training_config_service import (
    normalize_ml_intern_training_config,
)
from agent.services.ml_intern_training_contract import (
    UNSLOTH_BACKENDS,
    normalize_run_ids,
    normalize_source_ids,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_evidence import (
    EvidenceVerificationError,
    ProvidedEvidenceRegistry,
)
from agent.services.unsloth_storage_governance_service import (
    SqliteUnslothStorageCatalog,
    storage_catalog_from_config,
)


class EvaluationProvenanceError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class MlInternTrainingResultPublisher(Protocol):
    def publish(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str: ...

    def publish_evaluation(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str: ...


class RegistryTrainingResultPublisher:
    """Verify a downloaded PEFT tree and advance its explicit lifecycle."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        registry_path: str | Path,
        minimum_eval_score: float = 0.0,
        base_model_catalog: Mapping[str, Any] | None = None,
        trusted_source_ids: tuple[str, ...] | list[str] = (),
        trusted_run_ids: tuple[str, ...] | list[str] = (),
        repository: MlInternTrainingRepository | None = None,
        storage_catalog: SqliteUnslothStorageCatalog | None = None,
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._security = MlInternArtifactSecurityService(storage_root=self._artifact_root)
        self._registry = MlInternAdapterRegistryService(registry_path)
        self._evaluations = MlInternEvaluationStoreService(
            artifact_root=self._artifact_root,
            storage_references=storage_catalog,
        )
        self._minimum_eval_score = max(0.0, float(minimum_eval_score))
        self._base_model_catalog = dict(base_model_catalog or {})
        self._trusted_evidence = ProvidedEvidenceRegistry(
            source_ids=trusted_source_ids,
            run_ids=trusted_run_ids,
        )
        self._repository = repository or get_ml_intern_training_repository()
        self._storage_catalog = storage_catalog

    def publish(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str:
        adapter_id = str(result.get("adapter_id") or f"adapter-{job.id}")
        tenant_scope = _tenant_scope_digest(job.tenant_id, job.owner_subject)
        binding = _validated_artifact_binding(
            job,
            result,
            expected_tenant_scope=tenant_scope,
        )
        attempt_id = str(getattr(job, "active_attempt_id", None) or "")
        if binding is not None:
            relative_adapter_dir = binding.relative_directory("adapter")
            adapter_dir = self._security.resolve_relative(
                relative_adapter_dir,
                must_exist=True,
            )
            attempt_id = binding.attempt_id
        else:
            relative_adapter_dir = f"jobs/{job.id}/adapter"
            adapter_dir = self._security.resolve_relative(
                relative_adapter_dir,
                must_exist=True,
            )
        inspected = self._security.validate_adapter_tree(adapter_dir)
        request_spec = dict(job.request_spec or {})
        dataset_hash = str(request_spec.get("dataset_hash") or "").strip().lower() or None
        if dataset_hash is not None and (
            len(dataset_hash) != 64 or any(character not in "0123456789abcdef" for character in dataset_hash)
        ):
            raise ValueError("training job contains an invalid canonical dataset_hash")
        source_ids = normalize_source_ids(request_spec.get("source_ids"))
        run_ids = normalize_run_ids(request_spec.get("run_ids"))
        provenance_verified = request_spec.get("provenance_status") == "verified"
        if self._storage_catalog is not None and relative_adapter_dir.startswith("tenants/"):
            self._storage_catalog.register(
                tenant_id=job.tenant_id,
                owner_scope_digest=tenant_scope,
                artifact_id=adapter_id,
                kind="export",
                relative_ref=relative_adapter_dir,
                job_id=job.id,
                attempt_id=attempt_id,
                artifact_sha256=str(inspected["tree_sha256"]),
                size_bytes=int(inspected["total_bytes"]),
            )
        self._registry.register_trained(
            adapter_id=adapter_id,
            display_name=str(job.request_spec.get("output_name") or adapter_id)[:160],
            version="1",
            base_model=str(job.base_model or ""),
            method=str(job.request_spec.get("method") or "qlora"),
            artifact_paths={
                # Preserve the established registry/runtime contract: callers
                # receive an immediately usable, verified path.
                "adapter_dir": str(adapter_dir),
                # Keep the portable Hub-owned reference for storage
                # governance, cleanup and container-to-container transport.
                "adapter_storage_ref": relative_adapter_dir,
            },
            config_hash=make_config_hash(dict(job.request_spec or {})),
            artifact_sha256=str(inspected["tree_sha256"]),
            dataset_hash=dataset_hash,
            source_ids=list(source_ids),
            run_ids=list(run_ids),
            provenance_verified=provenance_verified,
            task_kinds=list(job.request_spec.get("task_kinds") or []),
            notes=f"verified worker result ({inspected['total_bytes']} bytes)",
            release_target=str(request_spec.get("release_target") or "") or None,
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
        )
        return adapter_id

    def publish_evaluation(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str:
        adapter_id = str(job.request_spec.get("adapter_id") or result.get("adapter_id") or "")
        if not adapter_id:
            raise ValueError("evaluation result has no adapter correlation")
        tenant_scope = _tenant_scope_digest(job.tenant_id, job.owner_subject)
        binding = _validated_artifact_binding(
            job,
            result,
            expected_tenant_scope=tenant_scope,
        )
        if binding is not None:
            result_relative = binding.relative_directory("artifacts")
            result_dir = self._security.resolve_relative(
                result_relative,
                must_exist=True,
            )
        else:
            result_dir = self._security.resolve_relative(
                f"jobs/{job.id}",
                must_exist=True,
            )
        report_path = self._security.ensure_internal_path(result_dir / "eval_report.json", must_exist=True)
        manifest_path = self._security.ensure_internal_path(
            result_dir / "evaluation_manifest.json",
            must_exist=True,
        )
        if report_path.is_symlink() or manifest_path.is_symlink():
            raise ValueError("evaluation result contains a symbolic link")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("evaluation result is not valid JSON") from exc
        if not isinstance(report, Mapping) or not isinstance(manifest, Mapping):
            raise ValueError("evaluation result must contain JSON objects")
        if str(manifest.get("job_id") or "") != job.id:
            raise ValueError("evaluation manifest does not match the Hub job")
        manifest_adapter = manifest.get("adapter")
        if not isinstance(manifest_adapter, Mapping) or str(manifest_adapter.get("adapter_id") or "") != adapter_id:
            raise ValueError("evaluation manifest does not match the registered adapter")
        promotion_evidence = None
        if str(job.backend or "") in UNSLOTH_BACKENDS:
            promotion_evidence = self._validate_unsloth_promotion_evidence(
                job,
                result,
                manifest,
                manifest_path,
            )
        metrics = dict(result.get("metrics") or report.get("metrics") or report)
        if not _has_base_adapter_metrics(metrics):
            raise ValueError("evaluation result has no base-vs-adapter metrics")
        decision = evaluate_adapter_metrics(metrics, minimum_score=self._minimum_eval_score)
        self._evaluations.save(
            MlInternTrainingPrincipal(job.tenant_id, job.owner_subject),
            adapter_id=adapter_id,
            dataset_id=str(job.dataset_id or ""),
            metrics=metrics,
            samples=metrics.get("samples") if isinstance(metrics.get("samples"), list) else None,
            evaluation_id=job.id,
            decision=decision,
            promotion_evidence=promotion_evidence,
        )
        self._registry.set_eval_report(
            adapter_id,
            eval_report_ref=job.id,
            eval_score=decision.score,
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
        )
        return adapter_id

    def _validate_unsloth_promotion_evidence(
        self,
        job: MlInternTrainingJobDB,
        result: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_path: Path,
    ) -> dict[str, Any]:
        if (
            manifest.get("schema_version") != "ananta.adapter-evaluation-manifest.v1"
            or manifest.get("contract_version") != "ananta.lora-training.v1"
            or manifest.get("backend") != job.backend
        ):
            raise EvaluationProvenanceError(
                "evaluation_manifest_contract_invalid",
                "Unsloth evaluation manifest contract is invalid",
            )
        hub_evidence = result.get("_hub_execution_evidence")
        if not isinstance(hub_evidence, Mapping):
            raise EvaluationProvenanceError(
                "evaluation_execution_evidence_missing",
                "Hub execution evidence is required for Unsloth evaluation",
            )
        attempt_id = str(hub_evidence.get("attempt_id") or "")
        fencing_token = hub_evidence.get("fencing_token")
        tenant_scope_digest = str(hub_evidence.get("tenant_scope_digest") or "")
        expected_scope = hashlib.sha256(
            (f"ananta.ml-intern-training.scope.v1\x00{job.tenant_id}\x00{job.owner_subject}").encode("utf-8")
        ).hexdigest()
        if (
            not attempt_id
            or attempt_id != str(job.active_attempt_id or "")
            or str(manifest.get("attempt_id") or "") != attempt_id
            or isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or manifest.get("fencing_token") != fencing_token
            or not secrets.compare_digest(tenant_scope_digest, expected_scope)
        ):
            raise EvaluationProvenanceError(
                "evaluation_execution_binding_mismatch",
                "Evaluation tenant, attempt, or fencing evidence does not match the Hub lease",
            )
        fencing_digest = hashlib.sha256(str(fencing_token).encode("utf-8")).hexdigest()

        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
        if dataset is None or not dataset.validation_storage_ref:
            raise EvaluationProvenanceError(
                "evaluation_dataset_evidence_missing",
                "Evaluation dataset evidence is unavailable",
            )
        canonical_dataset_hash = str((job.request_spec or {}).get("dataset_hash") or "").strip().lower()
        if not _is_sha256(canonical_dataset_hash) or not secrets.compare_digest(
            canonical_dataset_hash,
            str(dataset.content_sha256 or "").strip().lower(),
        ):
            raise EvaluationProvenanceError(
                "evaluation_dataset_hash_mismatch",
                "Evaluation dataset hash does not match the Hub catalog",
            )
        manifest_dataset = manifest.get("validation_dataset")
        if not isinstance(manifest_dataset, Mapping):
            raise EvaluationProvenanceError(
                "evaluation_dataset_manifest_invalid",
                "Worker validation dataset manifest is missing",
            )
        validation = manifest_dataset.get("validation")
        if not isinstance(validation, Mapping):
            raise EvaluationProvenanceError(
                "evaluation_dataset_manifest_invalid",
                "Worker validation split manifest is missing",
            )
        validation_path = Path(str(dataset.validation_storage_ref)).resolve(strict=True)
        validation_sha256 = _file_sha256(validation_path)
        validation_records = _jsonl_records(validation_path)
        if (
            str(manifest_dataset.get("dataset_id") or "") != str(job.dataset_id or "")
            or not secrets.compare_digest(
                str(validation.get("sha256") or ""),
                validation_sha256,
            )
            or validation.get("record_count") != validation_records
        ):
            raise EvaluationProvenanceError(
                "evaluation_dataset_manifest_mismatch",
                "Worker validation dataset does not match the Hub dataset",
            )
        validation_identity = _canonical_sha256(
            {
                "dataset_id": manifest_dataset.get("dataset_id"),
                "dataset_version": manifest_dataset.get("dataset_version"),
                "validation": dict(validation),
            }
        )
        if not secrets.compare_digest(
            str(manifest_dataset.get("identity_hash") or ""),
            validation_identity,
        ):
            raise EvaluationProvenanceError(
                "evaluation_dataset_identity_mismatch",
                "Worker validation dataset identity hash is invalid",
            )

        manifest_model = manifest.get("base_model")
        catalog_entry = self._base_model_catalog.get(str(job.base_model or ""))
        if not isinstance(manifest_model, Mapping) or not isinstance(catalog_entry, Mapping):
            raise EvaluationProvenanceError(
                "evaluation_base_model_evidence_missing",
                "Pinned base-model evidence is unavailable",
            )
        base_model_sha256 = str(catalog_entry.get("snapshot_hash") or "")
        if (
            str(manifest_model.get("model_id") or "") != str(job.base_model or "")
            or not _is_sha256(base_model_sha256)
            or not secrets.compare_digest(
                str(manifest_model.get("snapshot_hash") or ""),
                base_model_sha256,
            )
        ):
            raise EvaluationProvenanceError(
                "evaluation_base_model_hash_mismatch",
                "Worker base-model hash does not match the admitted catalog",
            )

        registry_record = self._registry.get(
            str((job.request_spec or {}).get("adapter_id") or ""),
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
        )
        manifest_adapter = manifest.get("adapter")
        if (
            registry_record is None
            or not isinstance(manifest_adapter, Mapping)
            or str(manifest_adapter.get("adapter_id") or "") != registry_record.adapter_id
            or not registry_record.artifact_sha256
        ):
            raise EvaluationProvenanceError(
                "evaluation_adapter_evidence_missing",
                "Tenant-bound adapter evidence is unavailable",
            )
        raw_adapter_path = registry_record.artifact_paths.get("adapter_dir") or registry_record.artifact_paths.get(
            "adapter_path"
        )
        raw_adapter_ref = str(raw_adapter_path or "")
        adapter_path = (
            self._security.ensure_internal_path(raw_adapter_ref, must_exist=True)
            if Path(raw_adapter_ref).is_absolute()
            else self._security.resolve_relative(raw_adapter_ref, must_exist=True)
        )
        inspected = self._security.validate_adapter_tree(adapter_path)
        if not secrets.compare_digest(
            str(inspected["tree_sha256"]),
            registry_record.artifact_sha256,
        ):
            raise EvaluationProvenanceError(
                "evaluation_adapter_registry_hash_mismatch",
                "Registered adapter tree no longer matches its immutable hash",
            )
        adapter_sha256 = _transport_tree_sha256(adapter_path)
        if not secrets.compare_digest(
            str(manifest_adapter.get("sha256") or ""),
            adapter_sha256,
        ):
            raise EvaluationProvenanceError(
                "evaluation_adapter_hash_mismatch",
                "Worker adapter hash does not match the registered artifact",
            )

        artifacts = result.get("artifacts")
        artifact_rows = (
            {str(item.get("name") or ""): item for item in artifacts if isinstance(item, Mapping)}
            if isinstance(artifacts, list)
            else {}
        )
        export_sha256 = _file_sha256(manifest_path)
        manifest_artifact = artifact_rows.get("evaluation_manifest.json")
        if not isinstance(manifest_artifact, Mapping) or not secrets.compare_digest(
            str(manifest_artifact.get("sha256") or ""),
            export_sha256,
        ):
            raise EvaluationProvenanceError(
                "evaluation_export_hash_mismatch",
                "Downloaded evaluation manifest hash is not worker-bound",
            )

        try:
            job_sources = normalize_source_ids((job.request_spec or {}).get("source_ids"))
            job_runs = normalize_run_ids((job.request_spec or {}).get("run_ids"))
            record_sources = normalize_source_ids(registry_record.source_ids)
            record_runs = normalize_run_ids(registry_record.run_ids)
            references = self._trusted_evidence.resolve(
                source_ids=tuple(dict.fromkeys((*record_sources, *job_sources))),
                run_ids=tuple(dict.fromkeys((*record_runs, *job_runs))),
            )
        except (ValueError, EvidenceVerificationError) as exc:
            reason_code = getattr(exc, "code", "evaluation_provenance_invalid")
            raise EvaluationProvenanceError(
                str(reason_code),
                "Evaluation Source/Run evidence is not trusted",
            ) from exc
        return {
            "job_id": job.id,
            "attempt_id": attempt_id,
            "fencing_token_digest": fencing_digest,
            "dataset_hash": canonical_dataset_hash,
            "validation_dataset_hash": validation_identity,
            "base_model_id": str(job.base_model or ""),
            "base_model_sha256": base_model_sha256,
            "adapter_id": registry_record.adapter_id,
            "adapter_sha256": adapter_sha256,
            "artifact_sha256": registry_record.artifact_sha256,
            "export_sha256": export_sha256,
            "source_ids": list(references.source_ids),
            "run_ids": list(references.run_ids),
        }


def _validated_artifact_binding(
    job: MlInternTrainingJobDB,
    result: Mapping[str, Any],
    *,
    expected_tenant_scope: str,
) -> MlInternTrainingArtifactBinding | None:
    """Resolve an exact Hub-owned attempt binding without trusting a path."""

    raw = result.get("_artifact_storage_binding")
    active_attempt_id = str(getattr(job, "active_attempt_id", None) or "")
    if raw is None:
        if not active_attempt_id:
            return None
        return MlInternTrainingArtifactBinding(
            tenant_scope_digest=expected_tenant_scope,
            job_id=job.id,
            attempt_id=active_attempt_id,
        )
    if not isinstance(raw, Mapping):
        raise ValueError("artifact storage binding must be an object")
    if not active_attempt_id:
        raise ValueError("artifact storage binding requires an authoritative active Hub attempt")
    binding = MlInternTrainingArtifactBinding.from_mapping(raw)
    if binding.tenant_scope_digest != expected_tenant_scope or binding.job_id != job.id:
        raise ValueError("artifact storage binding does not match the Hub job")
    if binding.attempt_id != active_attempt_id:
        raise ValueError("artifact storage binding does not match the active Hub attempt")
    return binding


def _has_base_adapter_metrics(metrics: Mapping[str, Any]) -> bool:
    return isinstance(metrics.get("base"), Mapping) and isinstance(metrics.get("adapter"), Mapping)


def build_result_publisher(config: Mapping[str, Any]) -> RegistryTrainingResultPublisher:
    artifact_root = str(config.get("artifact_root") or "artifacts/lora")
    runtime = config.get("lora_runtime") if isinstance(config.get("lora_runtime"), Mapping) else {}
    registry_path = str(runtime.get("adapter_registry_path") or f"{artifact_root}/adapter_registry.json")
    security = config.get("unsloth_security") if isinstance(config.get("unsloth_security"), Mapping) else {}
    storage_catalog = storage_catalog_from_config(normalize_ml_intern_training_config(config))
    return RegistryTrainingResultPublisher(
        artifact_root=artifact_root,
        registry_path=registry_path,
        minimum_eval_score=float(config.get("minimum_eval_score") or 0.0),
        base_model_catalog=(
            config.get("base_model_catalog") if isinstance(config.get("base_model_catalog"), Mapping) else {}
        ),
        trusted_source_ids=tuple(security.get("trusted_source_ids") or ()),
        trusted_run_ids=tuple(security.get("trusted_run_ids") or ()),
        storage_catalog=storage_catalog,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transport_tree_sha256(path: Path) -> str:
    children = sorted(item for item in path.rglob("*") if item.is_file())
    if not children or any(item.is_symlink() for item in path.rglob("*")):
        raise EvaluationProvenanceError(
            "evaluation_adapter_artifact_invalid",
            "Adapter tree is empty or contains symbolic links",
        )
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _jsonl_records(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _tenant_scope_digest(tenant_id: str, owner_subject: str) -> str:
    return hashlib.sha256(
        (f"ananta.ml-intern-training.scope.v1\x00{tenant_id}\x00{owner_subject}").encode("utf-8")
    ).hexdigest()
