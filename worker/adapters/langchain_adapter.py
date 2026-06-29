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
from worker.adapters.chain_runners import (
    LangChainRunnableRunner,
    SimplexRunner,
)
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
        # LCG-010: pass the config's embedding_provider_scope so the
        # retriever shares the embedding model with the rest of Ananta.
        # Pre-LCG callers (no config) get the default scope, which is
        # the same as before.
        self._retriever = CodeCompassRetriever(
            scope=self._config.embedding_provider_scope,
        )

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
        model_ref = self._config.model_provider_ref
        locality = "cloud" if self._config.mode == "cloud_gated" else "local"
        provider_diagnostics = {
            "model_ref": model_ref,
            "locality": locality,
            "external_calls": self._config.external_calls_allowed,
        }
        return WorkflowAdapterDescriptor(
            adapter_id="adapter.langchain",
            display_name="LangChain",
            kind="langchain",
            status=status,  # type: ignore[arg-type]
            enabled=enabled,
            reason=reason,
            capabilities=["dry_run", "rag_query", "summarize", "tool_chain", "code_review"],
            version="1.0",
            provider_diagnostics=provider_diagnostics,
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

        # Live path (LCG-007 v0.8 closure): actually call the LLM.
        # Two code paths:
        #  1) Framework installed -> use LangChain Runnable if available.
        #  2) Framework not installed -> deterministic simplex path
        #     (prompt + generate_text + artifact). The simplex path is
        #     the v0.7 default; it does NOT require langchain installed.
        # In both paths the budget guard, the policy gate, and the
        # audit log are honoured. The framework path is purely a
        # runner swap; the contract is identical.
        prompt = self._build_prompt(task_type, payload, context_sources)
        budget.record_step("llm_call")
        try:
            output_text, runner_label = self._invoke_runner(
                prompt=prompt, payload=payload, budget=budget,
            )
        except WorkerError as exc:
            self._audit.log("execute_failed", task_id=task_id, reason_code=exc.reason_code)
            raise
        execution_trace.append({"step": "llm_invoked", "runner": runner_label})

        # Produce artifact-first output (LCG-013)
        artifact_id = f"artifact-lc-{uuid.uuid4().hex[:12]}"
        output_format = str(payload.get("output_format") or "text")
        artifact_content: Any = output_text
        artifact_status = "created"
        if output_format == "json":
            import json as _json
            try:
                artifact_content = _json.loads(output_text)
            except (ValueError, TypeError):
                artifact_status = "partial"
                # artifact_content bleibt output_text string
        artifact = {
            "artifact_id": artifact_id,
            "artifact_type": task_type,
            "content": artifact_content,
            "sources": context_sources,
            "status": artifact_status,
            "runner": runner_label,
        }
        execution_trace.append({"step": "artifact_created", "artifact_id": artifact_id})

        self._audit.log("execute_complete", task_id=task_id, status="success",
                         runner=runner_label)

        return WorkflowArtifactResult(
            adapter_id="adapter.langchain",
            task_id=task_id,
            task_type=task_type,
            status="success",
            summary=f"LangChain {task_type} completed with {len(context_sources)} CodeCompass sources (runner={runner_label})",
            artifacts=[artifact],
            sources=context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
        )

    # ── Runner selection (LCG-007 v0.8 closure) ────────────────────────

    def _invoke_runner(self, *, prompt: str, payload: dict[str, Any],
                       budget: WorkflowBudgetGuard) -> tuple[str, str]:
        """Return (output_text, runner_label).

        Runner selection:
        - If `langchain` is importable, try real ChatModel first (LCG-038/039).
          If a ChatModel is built via lc_chat_model_factory, use LCEL chain.
          Otherwise fall back to LangChainRunnableRunner.
        - Otherwise, use SimplexRunner (prompt + generate_text()).
          This is the v0.7 default and requires no extras.
        - Both runners go through the same generate_text() entry
          point, so the LLM-side behaviour is identical; only the
          wrapping changes.
        """
        if self._langchain_available():
            # Try real ChatModel first (LCG-038)
            try:
                from worker.adapters.lc_chat_model_factory import build_lc_chat_model
                chat_model = build_lc_chat_model(self._config.model_provider_ref)
                if chat_model is not None:
                    from langchain_core.runnables import RunnableLambda
                    from langchain_core.messages import HumanMessage

                    def _lcel_call(input_dict):
                        msgs = [HumanMessage(content=str(input_dict.get("prompt", "")))]
                        result = chat_model.invoke(msgs)
                        return str(result.content) if hasattr(result, "content") else str(result)

                    chain = RunnableLambda(_lcel_call)
                    budget.record_step("langchain_chain_invoke")
                    try:
                        return str(chain.invoke({"prompt": prompt, "payload": payload})), "langchain_chain"
                    except Exception as exc:
                        raise WorkerError(
                            "langchain_chain_failed",
                            f"LCEL chain failed: {type(exc).__name__}: {exc}",
                        ) from exc
            except (ImportError, Exception):
                pass
            # Fallback to RunnableLambda runner
            return LangChainRunnableRunner().run(
                prompt=prompt, payload=payload, budget=budget,
                model_provider_ref=self._config.model_provider_ref,
            ), "langchain_runnable"
        return SimplexRunner().run(
            prompt=prompt, payload=payload, budget=budget,
            model_provider_ref=self._config.model_provider_ref,
        ), "simplex"

    def _build_prompt(self, task_type: str, payload: dict[str, Any],
                       context_sources: list[dict[str, Any]]) -> str:
        """Build a deterministic prompt from task type, payload, and context.

        The template is the documented contract; the framework runner
        and the simplex runner both consume the same string. Swapping
        the template is the supported way to evolve chain behaviour
        without touching the runner.
        """
        try:
            from agent.common.redaction import redact
            _redact = redact
        except ImportError:
            _redact = str
        query = _redact(str(payload.get("query") or payload.get("prompt") or ""))
        context_block = "\n\n".join(
            f"[{i+1}] {s.get('path','')}: {s.get('content','')[:300]}"
            for i, s in enumerate(context_sources)
        )
        sections = [
            f"Task: {task_type}",
            f"User query: {query}",
        ]
        if context_block:
            sections.append("Context (CodeCompass):\n" + context_block)
        return "\n\n".join(sections)

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

    # ── LCG-050: stream() ──────────────────────────────────────────────────────

    def stream(self, *, task_id: str, task_type: str,
               payload: dict[str, Any]):
        """Yield stream events for a chain execution.

        Policy gate (dry_run) is checked before yielding any events.
        Falls back to batch execute() when LCEL chain streaming is not available.
        """
        dry = self.dry_run(task_id=task_id, task_type=task_type, payload=payload)
        if dry.blocked:
            yield {
                "adapter_id": "adapter.langchain",
                "task_id": task_id,
                "event_type": "stream_blocked",
                "reason": dry.block_reason,
            }
            return

        # Try LCEL chain streaming when langchain is available
        if self._langchain_available() and self._config.is_live():
            try:
                from langchain_core.prompts import ChatPromptTemplate  # type: ignore[import]
                from langchain_core.output_parsers import StrOutputParser  # type: ignore[import]
                from worker.adapters.lc_chat_model_factory import build_lc_chat_model

                model = build_lc_chat_model(self._config.model_provider_ref)
                if model is not None:
                    prompt_template = ChatPromptTemplate.from_messages([
                        ("system", "You are a helpful assistant."),
                        ("human", "{query}"),
                    ])
                    chain = prompt_template | model | StrOutputParser()
                    query = str(payload.get("query") or payload.get("prompt") or "")
                    for chunk in chain.stream({"query": query}):
                        yield {
                            "adapter_id": "adapter.langchain",
                            "task_id": task_id,
                            "event_type": "token",
                            "token": chunk,
                        }
                    yield {
                        "adapter_id": "adapter.langchain",
                        "task_id": task_id,
                        "event_type": "stream_end",
                        "result": WorkflowArtifactResult(
                            adapter_id="adapter.langchain",
                            task_id=task_id,
                            task_type=task_type,
                            status="success",
                            summary="LangChain stream completed",
                            execution_trace=self._audit.snapshot(),
                            policy_decisions=self._policy.decisions_log(),
                        ).as_dict(),
                    }
                    return
            except Exception as exc:  # noqa: BLE001
                self._audit.log("stream_chain_failed", task_id=task_id, reason=str(exc)[:200])

        # Batch fallback: execute() then yield single stream_end
        result = self.execute(task_id=task_id, task_type=task_type, payload=payload)
        yield {
            "adapter_id": "adapter.langchain",
            "task_id": task_id,
            "event_type": "stream_end",
            "result": result.as_dict(),
        }

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
