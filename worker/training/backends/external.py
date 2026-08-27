"""Reusable adapter boundary for pinned, optional trainer CLIs."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypedDict

from ananta_contracts.training_backend import TrainingBackendCapability
from worker.training.backend_artifact_normalizer import BackendArtifactNormalizer
from worker.training.backend_config_compiler import BackendConfigCompiler, CompiledBackendConfig
from worker.training.backend_installation import installed_package_version
from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.external_process import BoundedExternalTrainingProcess, ExternalProcessPort

CommandBuilder = Callable[[Path, Path], Sequence[str]]


class ExternalBackendDependencies(TypedDict, total=False):
    compiler: BackendConfigCompiler | None
    package_version: Callable[[str], str]
    executable_resolver: Callable[[str], str | None]
    runner_factory: Callable[[Path], ExternalProcessPort] | None


@dataclass(frozen=True, slots=True)
class ExternalBackendSpec:
    backend_id: str
    package_name: str
    version: str
    executable: str
    license_spdx: str
    maintenance: str
    command_builder: CommandBuilder


@dataclass(frozen=True, slots=True)
class PreparedExternalTraining:
    compiled: CompiledBackendConfig
    config_path: Path
    executable_path: Path


class ExternalCliTrainingBackend:
    """Translate, execute and normalize while remaining substitutable."""

    def __init__(
        self,
        spec: ExternalBackendSpec,
        *,
        compiler: BackendConfigCompiler | None = None,
        package_version: Callable[[str], str] = installed_package_version,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        runner_factory: Callable[[Path], ExternalProcessPort] | None = None,
    ) -> None:
        self._spec = spec
        self.name = spec.backend_id
        self._compiler = compiler or BackendConfigCompiler()
        self._package_version = package_version
        self._executable_resolver = executable_resolver
        self._runner_factory = runner_factory or (lambda path: BoundedExternalTrainingProcess(allowed_executable=path))

    def availability(self) -> tuple[bool, str | None]:
        try:
            installed = self._package_version(self._spec.package_name)
        except importlib.metadata.PackageNotFoundError:
            return False, f"missing dependency: {self._spec.package_name}"
        if installed != self._spec.version:
            return False, f"version mismatch: expected {self._spec.version}, observed {installed}"
        executable = self._executable_resolver(self._spec.executable)
        if not executable:
            return False, f"missing executable: {self._spec.executable}"
        return True, None

    def capability(self) -> TrainingBackendCapability:
        available, detail = self.availability()
        reason = "ok"
        if not available:
            reason = (
                "version_mismatch" if detail and detail.startswith("version mismatch") else "dependency_unavailable"
            )
        return TrainingBackendCapability(
            backend_id=self.name,
            backend_version=self._spec.version,
            available=available,
            reason_code=reason,
            maturity="experimental",
            maintenance=self._spec.maintenance,
            license_spdx=self._spec.license_spdx,
            modalities=("text",),
            objectives=("sft",),
            methods=("lora", "qlora"),
            precisions=("bf16", "fp16"),
            quantizations=("4bit", "none"),
            distributed_modes=("single_device",),
            exports=("adapter",),
            resume=True,
            evaluation=True,
            resource_profiles=("generic-safe", "rtx3080-safe"),
        )

    def prepare(self, context: TrainingContext) -> PreparedExternalTraining:
        available, detail = self.availability()
        if not available:
            code = "version_mismatch" if detail and detail.startswith("version mismatch") else "dependency_unavailable"
            raise TrainingBackendError(code, detail or "external training backend is unavailable")
        context.emit("phase", {"phase": "compiling_backend_config"})
        compiled = self._compiler.compile(self.name, context)
        config_path = compiled.write(context.artifact_root / "backend-config.json")
        executable = self._executable_resolver(self._spec.executable)
        if not executable:
            raise TrainingBackendError("dependency_unavailable", "external trainer executable disappeared")
        return PreparedExternalTraining(compiled=compiled, config_path=config_path, executable_path=Path(executable))

    def train(self, context: TrainingContext, prepared: PreparedExternalTraining) -> dict[str, Any]:
        context.emit("phase", {"phase": "training", "step": 0})
        runner = self._runner_factory(prepared.executable_path)
        runner.run(
            self._spec.command_builder(prepared.executable_path, prepared.config_path),
            cwd=context.artifact_root.parent,
            cancel=context.cancel,
            deadline_epoch_ms=context.request.deadline_epoch_ms,
        )
        return {"config_sha256": prepared.compiled.sha256}

    def evaluate(
        self,
        context: TrainingContext,
        prepared: PreparedExternalTraining,
        trained: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        context.emit("phase", {"phase": "evaluating"})
        metrics = _read_metrics(context.artifact_root)
        return {
            "backend": self.name,
            "backend_version": self._spec.version,
            "configuration_sha256": prepared.compiled.sha256,
            "validation_records": context.dataset.validation_records,
            "reported": metrics,
        }

    def save(
        self,
        context: TrainingContext,
        prepared: PreparedExternalTraining,
        trained: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        context.emit("phase", {"phase": "normalizing_artifacts"})
        root = context.artifact_root.resolve()
        candidates = []
        allowed_names = {
            "adapter_config.json",
            "adapter_model.safetensors",
            "backend-config.json",
            "evaluation.json",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        for path in root.rglob("*"):
            if path.name in allowed_names and path.is_file() and not path.is_symlink():
                candidates.append(path)
        weight = next((path for path in candidates if path.name == "adapter_model.safetensors"), None)
        config = next((path for path in candidates if path.name == "adapter_config.json"), None)
        if weight is None or config is None:
            raise TrainingBackendError(
                "artifact_invalid", "external backend did not produce a safetensors adapter and adapter config"
            )
        evaluation = root / "evaluation.json"
        evaluation.write_text(json.dumps(dict(metrics), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        normalized = BackendArtifactNormalizer().normalize(
            artifact_root=root,
            candidates={*candidates, evaluation},
            binding={
                "backend": self.name,
                "backend_version": self._spec.version,
                "configuration_sha256": prepared.compiled.sha256,
                "base_model_sha256": context.request.base_model.snapshot_hash,
                "dataset_sha256": context.dataset.dataset_hash,
                "job_id": context.request.job_id,
                "attempt_id": context.request.attempt_id,
            },
        )
        best = _best_checkpoint(context.checkpoint_root)
        return TrainingOutcome(metrics=metrics, artifacts=normalized.paths, best_checkpoint=best)


def _read_metrics(root: Path) -> Mapping[str, Any]:
    for name in ("trainer_state.json", "all_results.json", "train_results.json"):
        matches = tuple(root.rglob(name))
        if not matches:
            continue
        path = matches[0]
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise TrainingBackendError("evaluation_failed", "external metric artifact is unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrainingBackendError("evaluation_failed", "external metric artifact is invalid") from exc
        if isinstance(value, Mapping):
            return {
                str(key): item for key, item in value.items() if isinstance(item, (bool, int, float, str, type(None)))
            }
    return {}


def _best_checkpoint(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.name.startswith("checkpoint-") and not path.is_symlink()]
    return sorted(candidates, key=lambda path: path.name)[-1] if candidates else None


__all__ = [
    "ExternalBackendDependencies",
    "ExternalBackendSpec",
    "ExternalCliTrainingBackend",
    "PreparedExternalTraining",
]
