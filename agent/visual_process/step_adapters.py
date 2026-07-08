"""VP-native step adapters (VPEXEC-002, VPEXEC-003).

These run directly in the hub process (no worker dispatch).
Registered by get_step_executor() on first use.

Adapters implemented here:
  query_rewrite       — rewrite_query() synonym expansion
  rerank              — Reranker token-overlap boost
  embed_api           — HashEmbeddingProvider / OpenAICompatibleEmbeddingProvider
  sign_rotation       — DeterministicSignRotation (TQ-011)
  turboquant_mse      — TurboQuantMseEncoder (TQ-012, experimental)
  workspace_snapshot  — WorkspaceDiffService.take_before_snapshot()
  workspace_diff      — WorkspaceDiffService.compute_diff() + synthesize_manifest()
  ml_intern_build_lora_dataset — MlInternLoraDatasetBuildService.build_dataset
  ml_intern_train_lora — MlInternTrainingJobService.train_lora

CodeCompass (codecompass_*), Evolution (evolution_*), and domain_cluster
have implementation_state=registered_only — no adapter here, dry-run marks
them not_executable until dedicated adapters are built.
"""
from __future__ import annotations

from typing import Any

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_executor import StepAdapter, StepExecutionResult


# ── Query rewrite ─────────────────────────────────────────────────────────────

class QueryRewriteAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "query_rewrite"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.query_rewrite import rewrite_query
        query = str(artifacts.get("query") or step.metadata.get("query") or "")
        result = rewrite_query(query)
        return StepExecutionResult(
            status="success",
            outputs=result,
            backend_service="rewrite_query",
            executable=True,
            execution_reason="vp_adapter: synonym expansion (deterministic, no LLM, no network)",
        )


# ── ML-Intern LoRA dataset build + training ──────────────────────────────────

class MlInternBuildLoraDatasetAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "ml_intern_build_lora_dataset"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from agent.services.ml_intern_lora_dataset_build_service import (
            DatasetBuildError,
            get_lora_dataset_build_service,
        )

        metadata = dict(step.metadata or {})
        dataset_root = str(
            metadata.get("dataset_root")
            or metadata.get("datasetRoot")
            or context.get("dataset_root")
            or "data/training/lora"
        )
        spec = self._build_spec(metadata, artifacts)
        try:
            result = get_lora_dataset_build_service(dataset_root).build_dataset(spec)
        except DatasetBuildError as exc:
            return StepExecutionResult(
                status="failed",
                outputs={},
                diagnostics={"error": str(exc)},
                backend_service="MlInternLoraDatasetBuildService",
                executable=True,
                execution_reason="ml_intern_build_lora_dataset: invalid build spec",
            )

        status = "success" if result.status == "completed" else "failed"
        return StepExecutionResult(
            status=status,
            outputs={
                "dataset_build_result": result.to_dict(),
                "dataset_path": result.dataset_path,
                "absolute_dataset_path": result.absolute_dataset_path,
                "validation_report_path": result.report_path,
                "dataset_status": result.status,
            },
            diagnostics={"errors": result.errors, "skipped_records": result.skipped_records},
            warnings=list(result.warnings),
            backend_service="MlInternLoraDatasetBuildService",
            executable=True,
            execution_reason=f"vp_adapter: ml_intern build_lora_dataset status={result.status}",
        )

    @staticmethod
    def _build_spec(metadata: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
        return {
            "records": (
                artifacts.get("training_examples")
                or artifacts.get("examples")
                or artifacts.get("records")
                or metadata.get("records")
                or metadata.get("examples")
                or []
            ),
            "source_paths": (
                artifacts.get("source_paths")
                or metadata.get("source_paths")
                or metadata.get("sourcePaths")
                or []
            ),
            "output_path": str(metadata.get("output_path") or metadata.get("outputPath") or "vp-train.jsonl"),
            "format": str(metadata.get("format") or "instruction"),
            "max_examples": metadata.get("max_examples", metadata.get("maxExamples", 1000)),
            "min_instruction_chars": metadata.get("min_instruction_chars", metadata.get("minInstructionChars", 4)),
            "min_output_chars": metadata.get("min_output_chars", metadata.get("minOutputChars", 1)),
            "require_secret_scan": metadata.get("require_secret_scan", metadata.get("requireSecretScan", True)),
        }


class MlInternTrainLoraAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "ml_intern_train_lora"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from agent.services.ml_intern_training_job_service import get_training_job_service

        metadata = dict(step.metadata or {})
        dataset_path = str(
            artifacts.get("dataset_path")
            or metadata.get("dataset_path")
            or metadata.get("datasetPath")
            or ""
        ).strip()
        base_model = str(
            artifacts.get("base_model")
            or metadata.get("base_model")
            or metadata.get("baseModel")
            or ""
        ).strip()
        if not dataset_path:
            return StepExecutionResult(
                status="failed",
                executable=True,
                backend_service="MlInternTrainingJobService",
                execution_reason="ml_intern_train_lora: dataset_path is required",
            )
        if not base_model:
            return StepExecutionResult(
                status="failed",
                executable=True,
                backend_service="MlInternTrainingJobService",
                execution_reason="ml_intern_train_lora: base_model is required",
            )

        training_cfg = self._training_config(metadata, context)
        job_spec = self._job_spec(metadata, artifacts, dataset_path=dataset_path, base_model=base_model)
        result = get_training_job_service(training_cfg).submit_job(job_spec)
        status = "success" if result.status in {"dry_run_completed", "completed", "trained"} else "failed"
        return StepExecutionResult(
            status=status,
            outputs={
                "job_result": result.to_dict(),
                "job_id": result.job_id,
                "artifact_dir": result.artifact_dir,
                "training_status": result.status,
            },
            diagnostics={"job_type": result.job_type, "errors": result.errors},
            warnings=list(result.warnings),
            backend_service="MlInternTrainingJobService",
            executable=True,
            execution_reason=f"vp_adapter: ml_intern train_lora status={result.status}",
        )

    @staticmethod
    def _training_config(metadata: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("ml_intern_training") or {})
        cfg.update(dict(metadata.get("training_config") or metadata.get("trainingConfig") or {}))
        if "enabled" not in cfg:
            cfg["enabled"] = bool(metadata.get("enabled", False))
        if "mode" not in cfg:
            cfg["mode"] = str(metadata.get("mode") or "dry_run")
        if "backend" not in cfg:
            cfg["backend"] = str(metadata.get("backend") or "mock")
        for key in (
            "artifact_root",
            "dataset_root",
            "gpu_profile",
            "timeout_seconds",
            "require_dataset_validation",
            "require_secret_scan",
            "external_network_allowed",
        ):
            if key in metadata and key not in cfg:
                cfg[key] = metadata[key]
        return cfg

    @staticmethod
    def _job_spec(
        metadata: dict[str, Any],
        artifacts: dict[str, Any],
        *,
        dataset_path: str,
        base_model: str,
    ) -> dict[str, Any]:
        spec = {
            "job_type": "train_lora",
            "base_model": base_model,
            "dataset_path": dataset_path,
            "method": str(metadata.get("method") or "qlora"),
            "output_dir": str(metadata.get("output_dir") or metadata.get("outputDir") or "vp-lora-adapter"),
        }
        for key in (
            "batch_size",
            "max_seq_length",
            "gradient_accumulation_steps",
            "learning_rate",
            "lora_rank",
            "lora_alpha",
            "lora_dropout",
            "load_in_4bit",
            "max_steps",
            "num_train_epochs",
            "target_modules",
            "explicit_override",
        ):
            if key in artifacts:
                spec[key] = artifacts[key]
            elif key in metadata:
                spec[key] = metadata[key]
        return spec


# ── Reranker ──────────────────────────────────────────────────────────────────

class RerankAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "rerank"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.reranker import Reranker
        query = str(artifacts.get("query") or step.metadata.get("query") or "")
        candidates = list(artifacts.get("candidates") or [])
        weight = float(step.metadata.get("weight") or 0.15)
        enabled = bool(step.metadata.get("enabled", True))
        reranker = Reranker(enabled=enabled, weight=weight)
        reranked = reranker.rerank(query=query, candidates=candidates)
        return StepExecutionResult(
            status="success",
            outputs={"reranked": reranked, "count": len(reranked)},
            backend_service="Reranker",
            executable=True,
            execution_reason="vp_adapter: token-overlap boost (deterministic, no LLM)",
        )


# ── Embed API ─────────────────────────────────────────────────────────────────

class EmbedApiAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "embed_api"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        texts_raw = artifacts.get("texts") or step.metadata.get("texts") or []
        texts: list[str] = [texts_raw] if isinstance(texts_raw, str) else list(texts_raw)
        if not texts:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="embed_api: no texts provided in artifacts['texts'] or metadata['texts']",
                backend_service="EmbeddingProvider",
            )
        provider_name = str(step.metadata.get("provider") or "hash")
        try:
            provider = self._build_provider(step, provider_name)
            embeddings = provider.embed_texts(texts)
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"embed_api: provider={provider_name!r} failed",
                backend_service=f"EmbeddingProvider({provider_name})",
            )
        return StepExecutionResult(
            status="success",
            outputs={"embeddings": embeddings, "count": len(embeddings), "provider": provider_name},
            backend_service=f"EmbeddingProvider({provider_name})",
            executable=True,
            execution_reason=f"vp_adapter: embed_api provider={provider_name!r}",
        )

    @staticmethod
    def _build_provider(step: VisualProcessStep, provider_name: str) -> Any:
        from worker.retrieval.embedding_provider import (
            FakeEmbeddingProvider,
            HashEmbeddingProvider,
            OpenAICompatibleEmbeddingProvider,
        )
        dims = int(step.metadata.get("dimensions") or 12)
        if provider_name in ("hash", ""):
            return HashEmbeddingProvider(dimensions=dims)
        if provider_name == "fake":
            return FakeEmbeddingProvider(dimensions=max(dims, 4))
        if provider_name in ("openai_compatible", "openai"):
            return OpenAICompatibleEmbeddingProvider(
                base_url=str(step.metadata.get("base_url") or ""),
                model=str(step.metadata.get("model") or "text-embedding-3-small"),
                api_key=str(step.metadata.get("api_key") or ""),
                dimensions=max(dims, 1536),
            )
        raise ValueError(f"Unknown embedding provider: {provider_name!r}")


# ── Sign rotation (TQ-011) ────────────────────────────────────────────────────

class SignRotationAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "sign_rotation"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.turboquant_encoding import DeterministicSignRotation
        vector = list(artifacts.get("vector") or step.metadata.get("vector") or [])
        if not vector:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="sign_rotation: no vector provided",
                backend_service="DeterministicSignRotation",
            )
        seed = int(step.metadata.get("seed") or 888)
        rotation = DeterministicSignRotation(seed=seed)
        rotated = rotation.apply(vector)
        return StepExecutionResult(
            status="success",
            outputs={"rotated": rotated, "dim": len(rotated), "seed": seed},
            backend_service="DeterministicSignRotation (TQ-011)",
            executable=True,
            execution_reason="vp_adapter: SHA256 sign-flip, self-inverse, deterministic (production)",
        )


# ── TurboQuant MSE (TQ-012, experimental) ────────────────────────────────────

class TurboQuantMseAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "turboquant_mse"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.turboquant_encoding import TurboQuantMseEncoder
        vector = list(artifacts.get("vector") or step.metadata.get("vector") or [])
        if not vector:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="turboquant_mse: no vector provided",
                backend_service="TurboQuantMseEncoder",
                warnings=["TQ-012 is experimental (no production codebook). TQ-013 ProdStub is a separate unused stub."],
            )
        seed = int(step.metadata.get("seed") or 888)
        levels = int(step.metadata.get("levels") or 7)
        encoder = TurboQuantMseEncoder(seed=seed, levels=levels)
        encoded = encoder.encode(vector)
        outputs: dict[str, Any] = (
            dict(encoded) if isinstance(encoded, dict) else {"quantized": encoded}
        )
        outputs.update({"seed": seed, "levels": levels})
        return StepExecutionResult(
            status="success",
            outputs=outputs,
            backend_service="TurboQuantMseEncoder (TQ-012)",
            executable=True,
            execution_reason="vp_adapter: sign-rotate + 4-bit scalar quant (experimental PoC, deterministic)",
            warnings=["TQ-012 is experimental (no production codebook). TQ-013 ProdStub is a separate unused stub."],
        )


# ── Workspace snapshot ────────────────────────────────────────────────────────

class WorkspaceSnapshotAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "workspace_snapshot"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        import os
        from pathlib import Path
        try:
            from agent.services.workspace_diff_service import WorkspaceDiffService
        except ImportError as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason="workspace_snapshot: WorkspaceDiffService not available",
                backend_service="WorkspaceDiffService",
            )
        workspace_root = Path(str(
            step.metadata.get("workspace_root")
            or artifacts.get("workspace_root")
            or context.get("workspace_root")
            or os.getcwd()
        ))
        if not workspace_root.exists():
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"workspace_root": str(workspace_root)},
                execution_reason=f"workspace_snapshot: path does not exist: {workspace_root}",
                backend_service="WorkspaceDiffService",
            )
        try:
            svc = WorkspaceDiffService()
            snapshot_id, file_map = svc.take_before_snapshot(workspace_root)
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"workspace_snapshot: take_before_snapshot failed: {exc}",
                backend_service="WorkspaceDiffService",
            )
        return StepExecutionResult(
            status="success",
            outputs={"snapshot_id": snapshot_id, "file_map": file_map, "file_count": len(file_map)},
            backend_service="WorkspaceDiffService.take_before_snapshot",
            executable=True,
            execution_reason="vp_adapter: workspace hash-map snapshot (deterministic, read-only)",
        )


# ── Workspace diff ────────────────────────────────────────────────────────────

class WorkspaceDiffAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "workspace_diff"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        import os
        from pathlib import Path
        try:
            from agent.services.workspace_diff_service import WorkspaceDiffService
        except ImportError as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason="workspace_diff: WorkspaceDiffService not available",
                backend_service="WorkspaceDiffService",
            )
        workspace_root = Path(str(
            step.metadata.get("workspace_root")
            or artifacts.get("workspace_root")
            or context.get("workspace_root")
            or os.getcwd()
        ))
        before_snapshot_id = str(artifacts.get("before_snapshot_id") or "before")
        before_snapshot: dict[str, str] = dict(artifacts.get("before_snapshot") or {})
        after_snapshot_id = str(artifacts.get("after_snapshot_id") or "after")
        after_snapshot: dict[str, str] = dict(artifacts.get("after_snapshot") or {})
        task_id = str(context.get("task_id") or step.id)
        execution_id = str(context.get("execution_id") or "vp-exec")
        try:
            svc = WorkspaceDiffService()
            diff = svc.compute_diff(
                task_id=task_id,
                execution_id=execution_id,
                workspace_root=workspace_root,
                before_snapshot_id=before_snapshot_id,
                before_snapshot=before_snapshot,
                after_snapshot_id=after_snapshot_id,
                after_snapshot=after_snapshot,
            )
            manifest = svc.synthesize_manifest(
                file_change_set=diff,
                workspace_root=workspace_root,
                task_id=task_id,
                goal_id=str(context.get("goal_id") or ""),
                execution_id=execution_id,
                trace_id=str(context.get("trace_id") or ""),
            )
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"workspace_diff: compute_diff/synthesize_manifest failed: {exc}",
                backend_service="WorkspaceDiffService",
            )
        return StepExecutionResult(
            status="success",
            outputs={"manifest": manifest},
            backend_service="WorkspaceDiffService.compute_diff + synthesize_manifest",
            executable=True,
            execution_reason="vp_adapter: workspace diff → artifact_manifest.v1 (deterministic)",
        )
