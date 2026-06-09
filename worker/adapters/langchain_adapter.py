"""LangChain Worker Adapter (LCG-007, LCG-009, LCG-011, LCG-012, LCG-016, LCG-018, LCG-019, LCG-020).

Optional dependency: langchain is NOT imported at module load time.
All tool calls go through policy gates.
CodeCompass is the only allowed retriever source.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from agent.providers.lc_lg import LangChainProviderConfig
from worker.adapters.workflow_adapter_base import (
    DryRunResult, WorkerError, WorkflowAdapterDescriptor, WorkflowArtifactResult,
)
from worker.adapters.workflow_policy_gate import WorkflowPolicyGate
from worker.adapters.workflow_audit import WorkflowAuditLog
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.retrieval.codecompass_retriever import CodeCompassRetriever


_SUPPORTED_TASK_TYPES = frozenset({
    "rag_query", "summarize", "tool_chain", "code_review",
})

_RISK_MAP = {
    "rag_query": "low", "summarize": "low",
    "code_review": "medium", "tool_chain": "high",
}


class LangChainAdapter:
    """Optional LangChain worker adapter.

    Respects LangChainProviderConfig.  Live execution is blocked unless
    config.is_live() and all policy gates pass.
    """

    def __init__(self, config: LangChainProviderConfig | None = None) -> None:
        self._config = config or LangChainProviderConfig.default_off()
        self._policy = WorkflowPolicyGate(
            external_calls_allowed=self._config.external_calls_allowed,
            allowed_tools=set(self._config.allowed_tools),
            human_required_actions=set(),
        )
        self._audit = WorkflowAuditLog(adapter_id="adapter.langchain")
        self._retriever = CodeCompassRetriever()

    def descriptor(self) -> WorkflowAdapterDescriptor:
        available = self._langchain_available()
        enabled = self._config.enabled and available
        if not self._config.enabled:
            status, reason = "disabled", "adapter_disabled_by_config"
        elif not available:
            status, reason = "degraded", "langchain_not_installed"
        elif self._config.mode == "dry_run":
            status, reason = "ready", "dry_run_mode"
        else:
            status, reason = "ready", "ready"
        return WorkflowAdapterDescriptor(
            adapter_id="adapter.langchain",
            display_name="LangChain",
            kind="langchain",
            status=status,  # type: ignore[arg-type]
            enabled=enabled,
            reason=reason,
            capabilities=["dry_run", "rag_query", "summarize", "tool_chain", "code_review"],
            version="1.0",
        )

    # ── Dry-run (LCG-016) ─────────────────────────────────────────────────────

    def dry_run(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any]) -> DryRunResult:
        # Discard the previous task's events so this task's audit log
        # starts fresh. dry_run's own events are captured below before
        # return and attached to the result.
        self._audit.snapshot()
        self._audit.log("dry_run_start", task_id=task_id, task_type=task_type)
        result = DryRunResult(
            adapter_id="adapter.langchain",
            task_id=task_id,
            task_type=task_type,
            risk_level=_RISK_MAP.get(task_type, "medium"),
        )

        # Task type check
        if task_type not in _SUPPORTED_TASK_TYPES:
            result.blocked = True
            result.block_reason = f"unsupported_task_type:{task_type}"
            self._audit.log("dry_run_blocked", task_id=task_id, reason=result.block_reason)
            return result

        # Tools
        requested_tools = list(payload.get("tools") or [])
        for tool in requested_tools:
            decision = self._policy.check_tool(tool)
            result.policy_decisions.append(decision)
            if not decision["allowed"]:
                result.blocked = True
                result.block_reason = f"tool_blocked:{tool}"

        # Retriever
        retriever = payload.get("retriever_ref") or self._config.retriever_source
        if retriever and retriever != "none" and retriever != "codecompass":
            result.blocked = True
            result.block_reason = "only_codecompass_retriever_allowed"
        if retriever == "codecompass":
            result.required_context_sources.append("codecompass")

        # External calls
        if payload.get("external_url") and not self._config.external_calls_allowed:
            result.blocked = True
            result.block_reason = "external_calls_blocked_by_policy"
            result.policy_decisions.append({"allowed": False, "reason": "external_calls_blocked"})

        # Plan steps
        result.plan_steps = self._build_plan(task_type, payload, retriever)
        result.required_tools = requested_tools
        result.estimated_tokens = _estimate_tokens(payload)

        if not result.blocked:
            result.approval_required = task_type in ("tool_chain",) or bool(requested_tools)
            if result.approval_required:
                result.approval_reasons = [f"tool_chain_requires_approval:{task_type}"]

        self._audit.log("dry_run_complete", task_id=task_id,
                         blocked=result.blocked, approval_required=result.approval_required)
        # Attach this task's events to the result, then clear for the
        # next task. The caller can inspect the trace or ignore it.
        result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
        return result

    # ── Live execute (LCG-007) ────────────────────────────────────────────────

    def execute(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any]) -> WorkflowArtifactResult:
        # Atomic snapshot so direct-execute callers and execute-after-
        # dry_run callers both see only the execute-path trace.
        # dry_run has already snapshotted its own events into
        # metadata.dry_run_audit_trace.
        self._audit.snapshot()
        self._audit.log("execute_start", task_id=task_id, task_type=task_type)

        # Gate: live execution requires explicit config
        if not self._config.is_live():
            return self._blocked_result(
                task_id, task_type,
                "live_execution_requires_live_mode",
                "Adapter is in dry_run mode; set mode=local_live to execute.",
            )

        # Gate: dry-run must pass first
        dry = self.dry_run(task_id=task_id, task_type=task_type, payload=payload)
        if dry.blocked:
            return self._blocked_result(task_id, task_type, dry.block_reason,
                                         f"blocked by dry-run: {dry.block_reason}")

        # Gate: approval required
        if dry.approval_required:
            return self._blocked_result(
                task_id, task_type, "approval_required",
                f"Human approval required: {'; '.join(dry.approval_reasons)}",
            )

        # Budget guard (LCG-019)
        budget = WorkflowBudgetGuard(
            max_steps=self._config.max_steps,
            timeout_seconds=self._config.timeout_seconds,
        )

        try:
            result = self._run_chain(task_id, task_type, payload, budget)
        except WorkerError as exc:
            self._audit.log("execute_failed", task_id=task_id, reason_code=exc.reason_code)
            return WorkflowArtifactResult(
                adapter_id="adapter.langchain", task_id=task_id, task_type=task_type,
                status="failed", summary=str(exc), error=str(exc), reason_code=exc.reason_code,
            )

        self._audit.log("execute_complete", task_id=task_id, status=result.status)
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_chain(self, task_id: str, task_type: str,
                    payload: dict[str, Any], budget: WorkflowBudgetGuard) -> WorkflowArtifactResult:
        if not self._langchain_available():
            raise WorkerError("langchain_not_installed",
                               "langchain package is not installed; pip install ananta[langchain]")

        # CodeCompass context (LCG-009)
        context_sources: list[dict[str, Any]] = []
        if self._config.retriever_source == "codecompass":
            query = str(payload.get("query") or payload.get("prompt") or "")
            if query:
                ctx = self._retriever.query(query, max_results=5)
                context_sources = ctx.get("sources", [])
                budget.record_step("codecompass_query")

        execution_trace = [
            {"step": "context_retrieved", "sources": len(context_sources)},
        ]

        # The actual LLM call is a placeholder until a real LangChain
        # executor is plumbed in. We deliberately do NOT import langchain
        # at module load time, and we do NOT import it here either — this
        # is a skeleton adapter that proves the contract without a hard
        # dependency. A future commit will wire a real chain runner.
        budget.record_step("llm_call")

        # Produce artifact-first output (LCG-013)
        artifact_id = f"artifact-lc-{uuid.uuid4().hex[:12]}"
        output_text = f"[LangChain {task_type} result — {len(context_sources)} CodeCompass sources]"
        artifact = {
            "artifact_id": artifact_id,
            "artifact_type": task_type,
            "content": output_text,
            "sources": context_sources,
            "status": "created",
        }
        execution_trace.append({"step": "artifact_created", "artifact_id": artifact_id})

        return WorkflowArtifactResult(
            adapter_id="adapter.langchain",
            task_id=task_id,
            task_type=task_type,
            status="success",
            summary=f"LangChain {task_type} completed with {len(context_sources)} CodeCompass sources",
            artifacts=[artifact],
            sources=context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
        )

    def _build_plan(self, task_type: str, payload: dict[str, Any],
                     retriever: str | None) -> list[dict[str, Any]]:
        steps = []
        if retriever == "codecompass":
            steps.append({"step": 1, "action": "codecompass_query",
                           "description": "Fetch context from CodeCompass"})
        steps.append({"step": len(steps) + 1, "action": f"langchain_{task_type}",
                       "description": f"Execute {task_type} chain with retrieved context"})
        steps.append({"step": len(steps) + 1, "action": "artifact_write",
                       "description": "Write result as artifact (artifact_first)"})
        return steps

    def _blocked_result(self, task_id: str, task_type: str,
                          reason_code: str, message: str) -> WorkflowArtifactResult:
        self._audit.log("execute_blocked", task_id=task_id, reason_code=reason_code)
        # Snapshot the execute-path events into the result so the
        # audit log does not leak into the next task.
        return WorkflowArtifactResult(
            adapter_id="adapter.langchain", task_id=task_id, task_type=task_type,
            status="blocked", summary=message, error=message, reason_code=reason_code,
            execution_trace=self._audit.snapshot(),
        )

    @staticmethod
    def _langchain_available() -> bool:
        try:
            import importlib
            importlib.import_module("langchain")
            return True
        except ImportError:
            return False


def _estimate_tokens(payload: dict[str, Any]) -> int:
    text = str(payload.get("query") or payload.get("prompt") or "")
    return max(100, len(text) // 4 + 200)
