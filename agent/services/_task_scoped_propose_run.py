"""Per-run state and collaborators of the single-backend task propose path.

Split out of ``_task_scoped_propose_single`` (SRP): the call-time resolved
task-scoped collaborators, the mutable propose-run state and the shared
repair/flow-metrics helpers used by both the CLI flow and proposal
persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


@dataclass(frozen=True)
class ProposeCollaborators:
    """Task-scoped helpers resolved at call time (so module-level patches of their sources apply)."""

    route_response: Any
    build_flow_metrics_payload: Callable
    update_task_flow_metrics: Callable
    compact_research_context: Callable
    native_worker_runtime_enabled: Callable
    has_native_opencode_runtime: Callable
    resolve_interactive_context_profile: Callable
    resolve_interactive_propose_timeout: Callable
    resolve_interactive_retry_timeout: Callable
    resolve_opencode_execution_mode: Callable
    normalize_temperature: Callable
    build_llm_call_profile_entries: Callable
    repair_task_proposal: Callable
    build_research_result: Callable
    build_review_state: Callable
    prepare_task_cli_session: Callable
    routing_dimensions: Callable
    build_task_propose_prompt: Callable

    @classmethod
    def load(cls) -> "ProposeCollaborators":
        from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
        from agent.services._task_scoped_citation import build_flow_metrics_payload, update_task_flow_metrics
        from agent.services._task_scoped_config_policy import (
            compact_research_context,
            native_worker_runtime_enabled,
            has_native_opencode_runtime,
            normalize_temperature,
            resolve_interactive_context_profile,
            resolve_interactive_propose_timeout,
            resolve_interactive_retry_timeout,
            resolve_opencode_execution_mode,
        )
        from agent.services._task_scoped_repair import build_llm_call_profile_entries, repair_task_proposal
        from agent.services._task_scoped_runtime import (
            build_research_result,
            build_review_state,
            prepare_task_cli_session,
            routing_dimensions,
            build_task_propose_prompt,
        )

        return cls(
            route_response=TaskScopedRouteResponse,
            build_flow_metrics_payload=build_flow_metrics_payload,
            update_task_flow_metrics=update_task_flow_metrics,
            compact_research_context=compact_research_context,
            native_worker_runtime_enabled=native_worker_runtime_enabled,
            has_native_opencode_runtime=has_native_opencode_runtime,
            resolve_interactive_context_profile=resolve_interactive_context_profile,
            resolve_interactive_propose_timeout=resolve_interactive_propose_timeout,
            resolve_interactive_retry_timeout=resolve_interactive_retry_timeout,
            resolve_opencode_execution_mode=resolve_opencode_execution_mode,
            normalize_temperature=normalize_temperature,
            build_llm_call_profile_entries=build_llm_call_profile_entries,
            repair_task_proposal=repair_task_proposal,
            build_research_result=build_research_result,
            build_review_state=build_review_state,
            prepare_task_cli_session=prepare_task_cli_session,
            routing_dimensions=routing_dimensions,
            build_task_propose_prompt=build_task_propose_prompt,
        )


@dataclass
class ProposeRun:
    """Immutable request facts plus the mutable CLI outcome of one propose run."""

    tid: str
    task: dict
    cfg: dict
    base_prompt: str
    research_context: dict | None
    cli_runner: Callable
    invoke_cli_runner: Callable
    coalesce_cli_output: Callable
    deps: ProposeCollaborators
    task_kind: str
    required_capabilities: Any
    research_specialization: Any
    effective_backend: str
    routing_reason: Any
    workspace_dir: str
    timeout: int
    proposal_model: Any
    policy_version: Any
    session_payload: Any
    interactive_terminal_session: bool
    requested_temperature: Any = None
    pipeline: dict = field(default_factory=dict)
    prompt_for_cli: str = ""
    worker_context_meta: dict = field(default_factory=dict)
    effective_research_context: Any = None
    rc: int = 0
    cli_out: Any = None
    cli_err: Any = None
    raw_res: Any = None
    output_source: Any = None
    backend_used: Any = None
    latency_ms: int = 0
    repair_meta: dict = field(default_factory=lambda: {"attempted": False, "backend": None, "model": None})
    interactive_retry_meta: dict = field(
        default_factory=lambda: {"attempted": False, "timeout": None, "latency_ms": None}
    )

    @property
    def interactive_opencode(self) -> bool:
        return self.interactive_terminal_session and self.backend_used == "opencode"

    @property
    def policy_classification(self) -> str | None:
        return str(self.routing_reason or "").strip().lower() or None

    def apply_repair(self, repaired: dict) -> None:
        self.raw_res = repaired["raw"]
        self.output_source = repaired["output_source"]
        self.backend_used = repaired["backend_used"]
        self.rc = int(repaired["rc"])
        self.cli_err = str(repaired.get("stderr") or "")
        self.repair_meta = {
            "attempted": True,
            "backend": repaired["backend_used"],
            "model": repaired.get("model"),
        }


def repair_proposal(run: ProposeRun, *, bad_output: str, validation_error: str) -> dict | None:
    return run.deps.repair_task_proposal(
        cli_runner=run.cli_runner,
        prompt=run.prompt_for_cli,
        bad_output=bad_output,
        validation_error=validation_error,
        timeout=run.timeout,
        task_kind=run.task_kind,
        policy_version=run.policy_version,
        cfg=run.cfg,
        primary_backend=run.backend_used,
        primary_model=run.proposal_model,
        primary_temperature=run.requested_temperature,
        research_context=run.effective_research_context,
        session=run.session_payload,
        workdir=run.workspace_dir,
        invoke_cli_runner=run.invoke_cli_runner,
        coalesce_cli_output=run.coalesce_cli_output,
        normalize_temperature=run.deps.normalize_temperature,
    )


def propose_flow_metrics(run: ProposeRun, *, run_id, propose_ok: bool, policy_classification) -> dict:
    return run.deps.build_flow_metrics_payload(
        run_id=run_id,
        phase="propose",
        propose_ok=propose_ok,
        execute_ok=None,
        artifact_created=None,
        worker_profile=run.worker_context_meta.get("worker_profile"),
        profile_source=run.worker_context_meta.get("profile_source"),
        policy_classification=policy_classification,
    )


__all__ = [
    "INTERACTIVE_TERMINAL_FINALIZE_COMMAND",
    "ProposeCollaborators",
    "ProposeRun",
    "propose_flow_metrics",
    "repair_proposal",
]
