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
