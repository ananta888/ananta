"""Publish verified worker outcomes into the adapter lifecycle registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent.db_models import MlInternTrainingJobDB
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    make_config_hash,
)
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_evaluation_decision_service import evaluate_adapter_metrics
from agent.services.ml_intern_evaluation_store_service import MlInternEvaluationStoreService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


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
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._security = MlInternArtifactSecurityService(storage_root=self._artifact_root)
        self._registry = MlInternAdapterRegistryService(registry_path)
        self._evaluations = MlInternEvaluationStoreService(artifact_root=self._artifact_root)
        self._minimum_eval_score = max(0.0, float(minimum_eval_score))

    def publish(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str:
        adapter_id = str(result.get("adapter_id") or f"adapter-{job.id}")
        adapter_dir = self._security.resolve_relative(f"jobs/{job.id}/adapter", must_exist=True)
        inspected = self._security.validate_adapter_tree(adapter_dir)
        self._registry.register_trained(
            adapter_id=adapter_id,
            display_name=str(job.request_spec.get("output_name") or adapter_id)[:160],
            version="1",
            base_model=str(job.base_model or ""),
            method=str(job.request_spec.get("method") or "qlora"),
            artifact_paths={"adapter_dir": str(adapter_dir)},
            config_hash=make_config_hash(dict(job.request_spec or {})),
            artifact_sha256=str(inspected["tree_sha256"]),
            task_kinds=list(job.request_spec.get("task_kinds") or []),
            notes=(
                f"verified worker result ({inspected['total_bytes']} bytes); "
                f"dataset={hashlib.sha256(str(job.dataset_id or '').encode()).hexdigest()}"
            ),
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
        )
        return adapter_id

    def publish_evaluation(self, job: MlInternTrainingJobDB, result: Mapping[str, Any]) -> str:
        adapter_id = str(job.request_spec.get("adapter_id") or result.get("adapter_id") or "")
        if not adapter_id:
            raise ValueError("evaluation result has no adapter correlation")
        result_dir = self._security.resolve_relative(f"jobs/{job.id}", must_exist=True)
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
        )
        self._registry.set_eval_report(
            adapter_id,
            eval_report_ref=job.id,
            eval_score=decision.score,
            tenant_id=job.tenant_id,
            owner_subject=job.owner_subject,
        )
        return adapter_id


def _has_base_adapter_metrics(metrics: Mapping[str, Any]) -> bool:
    return isinstance(metrics.get("base"), Mapping) and isinstance(metrics.get("adapter"), Mapping)


def build_result_publisher(config: Mapping[str, Any]) -> RegistryTrainingResultPublisher:
    artifact_root = str(config.get("artifact_root") or "artifacts/lora")
    runtime = config.get("lora_runtime") if isinstance(config.get("lora_runtime"), Mapping) else {}
    registry_path = str(runtime.get("adapter_registry_path") or f"{artifact_root}/adapter_registry.json")
    return RegistryTrainingResultPublisher(
        artifact_root=artifact_root,
        registry_path=registry_path,
        minimum_eval_score=float(config.get("minimum_eval_score") or 0.0),
    )
