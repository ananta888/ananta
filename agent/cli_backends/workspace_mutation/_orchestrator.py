"""AWWPI-013/014/015/016: workspace mutation loop for the ananta-worker.

This is the closed feedback loop the track demands — not batch
iteration: worker action -> hub check (DiffResult/PolicyResult/optional
TestResult) -> evidence feedback -> next worker action.

Modes (contract: ``docs/contracts/ananta-worker-mutation-mode.md``):

- ``controlled_workspace``: the model emits ``workspace_write`` actions
  which the runtime applies directly inside the hub-set boundaries
  (workspace root, materialization manifest, forbidden path filter);
  after every action the hub produces DiffResult + PolicyResult against
  the baseline.
- ``strict_patch_request``: direct writes are rejected; the model must
  emit ``patch_request`` (repo.apply_patch / repo.write_file) which the
  hub validates and applies one by one.

Loop end conditions: final_answer, max_iterations,
max_patch_attempts_per_file, policy_blocked, approval_required,
invalid_output_limit_reached, no_progress_detected. The final report is
written to ``.ananta/mutation-report.json``; the artifact sync path
reads it and never registers success artifacts for blocked runs
(AWWPI-017).
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any, Callable

from agent.cli_backends.helpers import _get_agent_config
from agent.cli_backends.context import default_context
from agent.cli_backends.tool_loop import (
    KIND_CANNOT_CONTINUE,
    KIND_FINAL_ANSWER,
    KIND_NEEDS_APPROVAL,
    KIND_TOOL_REQUEST,
)
from agent.cli_backends.workspace_mutation.prompts import (
    build_iteration_prompt as _build_iteration_prompt_impl,
    build_mode_instructions as _build_mode_instructions_impl,
    parse_mutation_output as _parse_mutation_output_impl,
)
from agent.cli_backends.workspace_mutation.signatures import (
    changes_signature as _changes_signature_impl,
    evidence_signature as _evidence_signature_impl,
)
from agent.cli_backends.workspace_mutation.handlers import ACTION_HANDLERS, KIND_WORKSPACE_WRITE
from agent.cli_backends.workspace_mutation.session import (
    LoopTurn,
    MutationLimits,
    MutationServices,
    MutationSession,
)
from agent.cli_backends.workspace_mutation.tool_actions import KIND_PATCH_REQUEST

log = logging.getLogger(__name__)

_MUTATION_KINDS = {
    KIND_TOOL_REQUEST,
    KIND_FINAL_ANSWER,
    KIND_NEEDS_APPROVAL,
    KIND_CANNOT_CONTINUE,
    KIND_WORKSPACE_WRITE,
    KIND_PATCH_REQUEST,
}

_MAX_EVIDENCE_BLOCKS = 8


def get_workspace_mutation_config(workdir: str | None = None) -> dict[str, Any]:
    """AWWPI-013: resolve config + effective mutation mode.

    Explicit ``mutation_mode`` from the research context (written by the
    hub) wins, then the configured mode, then the task_kind mapping. Risk
    rules escalate controlled_workspace to strict_patch_request.
    """
    agent_cfg = _get_agent_config()
    cfg = dict(agent_cfg.get("ananta_worker_workspace_mutation") or {})
    if "generated_source_line_policy" not in cfg and isinstance(agent_cfg.get("generated_source_line_policy"), dict):
        cfg["generated_source_line_policy"] = dict(agent_cfg.get("generated_source_line_policy") or {})
    task_kind = None
    risk = None
    explicit_mode = None
    if workdir:
        try:
            from agent.cli_backends.architecture_scan import _read_research_context

            research_context = _read_research_context(workdir)
            task_kind = str(research_context.get("task_kind") or "") or None
            risk = str(research_context.get("risk") or "") or None
            explicit_mode = str(research_context.get("mutation_mode") or "") or None
        except Exception:
            pass
    _mutation_policy_svc = default_context.ananta_workspace_mutation_policy_service

    resolved = _mutation_policy_svc.resolve_mutation_mode(
        cfg=cfg, task_kind=task_kind, risk=risk, explicit_mode=explicit_mode
    )
    return {
        **cfg,
        "enabled": bool(cfg.get("enabled", False)),
        "resolved_mode": resolved,
        "max_feedback_iterations": max(1, min(int(cfg.get("max_feedback_iterations") or 4), 16)),
        "max_patch_attempts_per_file": max(1, min(int(cfg.get("max_patch_attempts_per_file") or 3), 10)),
        "max_invalid_outputs": max(1, min(int(cfg.get("max_invalid_outputs") or 2), 10)),
        "max_diff_chars": _max_diff_chars(cfg),
    }


def _max_diff_chars(cfg: dict[str, Any]) -> int:
    """Diff/evidence block size from the central policy; an explicit ``max_diff_chars`` still applies within the
    working room of the effective window (the persisted historical 12000 counts as unset)."""
    from agent.context_profile import budget_override, context_budgets

    budgets = context_budgets()
    explicit = budget_override(cfg.get("max_diff_chars"), legacy="diff_chars")
    return max(500, min(explicit or budgets.chars("diff"), budgets.chars("tool_results_total")))


def parse_mutation_output(text: str) -> dict[str, Any] | None:
    """AWWPI-013: parse one model answer of the mutation loop.

    Delegates to the prompts sub-module (4-split extraction).
    """
    return _parse_mutation_output_impl(text)


def _build_mode_instructions(mode: str) -> str:
    return _build_mode_instructions_impl(mode)


def _evidence_signature(entry: dict[str, Any]) -> str:
    return _evidence_signature_impl(entry)


def _changes_signature(workspace: pathlib.Path, changed: list[str]) -> str:
    return _changes_signature_impl(workspace, changed)


def _build_iteration_prompt(
    *,
    original_prompt: str,
    instructions: str,
    evidence_blocks: list[dict[str, Any]],
    iteration: int,
    max_iterations: int,
    max_chars_per_block: int,
) -> str:
    return _build_iteration_prompt_impl(
        original_prompt=original_prompt,
        instructions=instructions,
        evidence_blocks=evidence_blocks,
        iteration=iteration,
        max_iterations=max_iterations,
        max_chars_per_block=max_chars_per_block,
    )


def run_ananta_worker_workspace_mutation(
    prompt: str,
    workdir: str,
    *,
    options: list,
    timeout: int,
    model: str | None,
    llm_runner: Callable[..., tuple[int, str, str]] | None = None,
    config: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> tuple[int, str, str]:
    """AWWPI-014/015/016: run the feedback mutation loop, return (rc, out, err)."""
    cfg = dict(config or get_workspace_mutation_config(workdir))
    mode = str(cfg.get("resolved_mode") or "read_only")
    if llm_runner is None:
        from agent.cli_backends.sgpt import run_sgpt_command

        llm_runner = run_sgpt_command

    session = MutationSession(
        cfg=cfg,
        mode=mode,
        workspace=pathlib.Path(workdir).resolve(),
        task_id=task_id,
        services=MutationServices(
            workspace=default_context.worker_workspace_service,
            mutation_policy=default_context.ananta_workspace_mutation_policy_service,
            tool_policy=default_context.ananta_tool_policy_service,
            source_line_policy=default_context.generated_source_line_policy_service,
        ),
        limits=MutationLimits(
            max_iterations=int(cfg.get("max_feedback_iterations") or 4),
            max_invalid=int(cfg.get("max_invalid_outputs") or 2),
            max_attempts_per_file=int(cfg.get("max_patch_attempts_per_file") or 3),
            max_diff_chars=int(cfg.get("max_diff_chars") or _max_diff_chars(cfg)),
        ),
    )
    instructions = _build_mode_instructions(mode)
    last_out, last_err = "", ""

    for iteration in range(1, session.limits.max_iterations + 1):
        iter_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            instructions=instructions,
            evidence_blocks=session.evidence_blocks,
            iteration=iteration,
            max_iterations=session.limits.max_iterations,
            max_chars_per_block=session.limits.max_diff_chars,
        )
        rc, out, err = llm_runner(
            prompt=iter_prompt, options=list(options or []), timeout=timeout, model=model, workdir=workdir
        )
        last_out, last_err = out, err
        if rc != 0 and not out:
            return session.finish("llm_failed", rc, out, err)

        message = parse_mutation_output(out)
        if message is None:
            result = _record_invalid_output(session, iteration, rc, out, err)
            if result is not None:
                return result
            continue

        kind = str(message.get("kind"))
        iteration_row: dict[str, Any] = {"iteration": iteration, "kind": kind}
        session.report_iterations.append(iteration_row)
        handler = ACTION_HANDLERS.get(kind)
        if handler is None:
            continue
        result = handler(session, LoopTurn(iteration=iteration, message=message, row=iteration_row, out=out, err=err))
        if result is not None:
            return result

    summary = {
        "kind": "loop_aborted",
        "reason": "max_iterations",
        "last_output": last_out[:2000],
        "last_policy_result": session.last_policy_result,
    }
    return session.finish_with_summary("max_iterations", summary, last_err)


def _record_invalid_output(
    session: MutationSession, iteration: int, rc: int, out: str, err: str
) -> tuple[int, str, str] | None:
    """Count a non-JSON answer; ends the loop once ``max_invalid_outputs`` is reached."""
    session.invalid_count += 1
    session.report_iterations.append({"iteration": iteration, "kind": "invalid_output"})
    if session.invalid_count >= session.limits.max_invalid:
        return session.finish("invalid_output_limit_reached", rc, out, err)
    session.add_evidence(
        {
            "kind": "protocol_warning",
            "iteration": iteration,
            "warning": "previous_answer_was_not_valid_mutation_json",
        }
    )
    return None
