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

import hashlib
import json
import logging
import pathlib
import time
import uuid
from typing import Any, Callable

from agent.cli_backends.helpers import _get_agent_config
from agent.cli_backends.context import default_context
from agent.common.sgpt_tool_loop import (
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
from agent.services.generated_source_line_policy_service import (
    DECISION_BLOCKED,
    extract_policy_config,
)

log = logging.getLogger(__name__)

KIND_WORKSPACE_WRITE = "workspace_write"
KIND_PATCH_REQUEST = "patch_request"

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
            from agent.common.sgpt_architecture_scan import _read_research_context

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
        "max_diff_chars": max(500, min(int(cfg.get("max_diff_chars") or 12000), 100000)),
    }


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
        from agent.common.sgpt import run_sgpt_command

        llm_runner = run_sgpt_command

    _tool_policy_svc = default_context.ananta_tool_policy_service
    _mutation_policy_svc = default_context.ananta_workspace_mutation_policy_service
    from agent.services.tools import execute_ananta_tool
    from agent.services.tools._evidence import build_tool_result
    from agent.services.tools.repo_tools import WorkspacePathError, resolve_workspace_path
    _ws_svc = default_context.worker_workspace_service

    workspace = pathlib.Path(workdir).resolve()
    ws_svc = _ws_svc
    _gen_source_line_policy = default_context.generated_source_line_policy_service
    mutation_policy = _mutation_policy_svc
    tool_policy = _tool_policy_svc

    session_id = uuid.uuid4().hex[:12]
    max_iterations = int(cfg.get("max_feedback_iterations") or 4)
    max_invalid = int(cfg.get("max_invalid_outputs") or 2)
    max_attempts_per_file = int(cfg.get("max_patch_attempts_per_file") or 3)
    max_diff_chars = int(cfg.get("max_diff_chars") or 12000)
    materialization_manifest = ws_svc.load_materialization_manifest(workspace)

    baseline_meta = ws_svc.refresh_mutation_baseline(workspace_dir=workspace, mutation_mode=mode)
    evidence_blocks: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    report_iterations: list[dict[str, Any]] = []
    file_attempts: dict[str, int] = {}
    invalid_count = 0
    tool_call_count = 0
    last_policy_result: dict[str, Any] | None = None
    last_source_line_policy_result: dict[str, Any] | None = None
    last_change_signature: str | None = None
    repeated_signature_count = 0
    last_rc, last_out, last_err = 0, "", ""

    def _add_evidence(entry: dict[str, Any]) -> None:
        signature = _evidence_signature(entry)
        if signature in seen_evidence:
            return
        seen_evidence.add(signature)
        evidence_blocks.append(entry)

    def _hub_check(
        *,
        iteration_number: int | None = None,
        ran_tests_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """DiffResult + PolicyResult against baseline (the hub observation step).

        ALWA-014: emits ``workspace_mutation_evaluated`` on every call
        with the canonical schema fields. The KIND_FINAL_ANSWER caller
        picks the action name based on the policy outcome and emits
        ``workspace_mutation_blocked`` for non-ok final policies.
        """
        nonlocal last_policy_result, last_source_line_policy_result
        changed = ws_svc.detect_changed_files_against_interactive_baseline(workspace_dir=workspace)
        meaningful = ws_svc.filter_meaningful_changed_files(changed)
        diff_text, diff_truncated = ws_svc.build_workspace_diff_text(
            workspace_dir=workspace, changed_rel_paths=meaningful, max_chars=max_diff_chars
        )
        policy_result = mutation_policy.evaluate_changed_files(
            workspace_dir=workspace,
            changed_rel_paths=meaningful,
            materialization_manifest=materialization_manifest,
            allowed_new_file_globs=list(cfg.get("allowed_new_file_globs") or []),
            require_materialized_scope=bool(cfg.get("require_materialized_scope", True)),
            strict_path_markers=list(cfg.get("strict_path_markers") or []) or None,
        )
        source_line_policy_result = _gen_source_line_policy.evaluate_changed_files(
            workspace_dir=workspace,
            changed_rel_paths=meaningful,
            cfg=extract_policy_config(cfg),
            baseline=None,
            context={"task_id": task_id},
        )
        last_policy_result = policy_result.as_dict()
        last_source_line_policy_result = source_line_policy_result.as_dict()
        check: dict[str, Any] = {
            "schema": "ananta_workspace_feedback.v1",
            "diff_result": {"changed_files": meaningful, "diff_excerpt": diff_text, "truncated": diff_truncated},
            "policy_result": last_policy_result,
            "source_line_policy_result": last_source_line_policy_result,
        }
        if ran_tests_result is not None:
            check["test_result"] = ran_tests_result
        # ALWA-014: emit workspace_mutation_evaluated with the full
        # required field set. diff_hash is computed over the truncated
        # diff_text (the same text the caller logs); diff_artifact_id
        # stays None here — the workspace.diff artifact is set by the
        # final sync path. We never read file contents.
        try:
            from agent.common.audit import (
                AUDIT_WORKSPACE_MUTATION_EVALUATED,
                audit_workspace_mutation_event,
            )
            blocked_changes = list(policy_result.blocked_changes or [])
            violation_ids = [
                f"{row.get('path','')}:{row.get('reason','')}"
                for row in blocked_changes
                if isinstance(row, dict)
            ]
            violation_summary = (
                "; ".join(
                    f"{row.get('path','')}:{row.get('reason','')}"
                    for row in blocked_changes
                    if isinstance(row, dict)
                )
                or None
            )
            diff_hash = (
                hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else None
            )
            audit_workspace_mutation_event(
                AUDIT_WORKSPACE_MUTATION_EVALUATED,
                task_id=task_id,
                iteration_number=iteration_number,
                mutation_mode=mode,
                changed_paths=meaningful,
                diff_hash=diff_hash,
                policy_decision=str(policy_result.status or "unknown"),
                violation_ids=violation_ids,
                violation_summary=violation_summary,
                source_line_policy_status=str(last_source_line_policy_result.get("status") or "ok"),
                source_line_policy_summary=dict(last_source_line_policy_result.get("summary") or {}),
            )
            from agent.common.audit import AUDIT_GENERATED_SOURCE_LINE_POLICY_EVALUATED

            audit_workspace_mutation_event(
                AUDIT_GENERATED_SOURCE_LINE_POLICY_EVALUATED,
                task_id=task_id,
                iteration_number=iteration_number,
                mutation_mode=mode,
                changed_paths=meaningful,
                policy_decision=str(last_source_line_policy_result.get("status") or "ok"),
                source_line_policy_summary=dict(last_source_line_policy_result.get("summary") or {}),
            )
            if str(last_source_line_policy_result.get("status") or "ok") in {"blocked", "followup_required"}:
                from agent.common.audit import AUDIT_GENERATED_SOURCE_LINE_POLICY_VIOLATION

                audit_workspace_mutation_event(
                    AUDIT_GENERATED_SOURCE_LINE_POLICY_VIOLATION,
                    task_id=task_id,
                    iteration_number=iteration_number,
                    mutation_mode=mode,
                    changed_paths=meaningful,
                    policy_decision=str(last_source_line_policy_result.get("status") or "unknown"),
                    violation_ids=[
                        f"{row.get('path','')}:{row.get('reason_code','')}"
                        for row in list(last_source_line_policy_result.get("file_results") or [])
                        if isinstance(row, dict) and str(row.get("decision") or "") in {"blocked", "followup_required"}
                    ],
                    source_line_policy_summary=dict(last_source_line_policy_result.get("summary") or {}),
                )
        except Exception:
            pass
        return check

    def _write_report(outcome: str) -> None:
        try:
            report_path = workspace / ".ananta" / "mutation-report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "ananta_worker_mutation_report.v1",
                        "session_id": session_id,
                        "task_id": task_id,
                        "mutation_mode": mode,
                        "outcome": outcome,
                        "baseline": baseline_meta,
                        "final_policy_result": last_policy_result,
                        "source_line_policy_summary": (
                            dict((last_source_line_policy_result or {}).get("summary") or {})
                            if last_source_line_policy_result
                            else None
                        ),
                        "final_source_line_policy_result": last_source_line_policy_result,
                        "invalid_output_count": invalid_count,
                        "tool_call_count": tool_call_count,
                        "created_at": time.time(),
                        "iterations": report_iterations,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _finish(outcome: str, rc: int, out: str, err: str) -> tuple[int, str, str]:
        _write_report(outcome)
        return rc, out, err

    instructions = _build_mode_instructions(mode)

    for iteration in range(1, max_iterations + 1):
        iter_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            instructions=instructions,
            evidence_blocks=evidence_blocks,
            iteration=iteration,
            max_iterations=max_iterations,
            max_chars_per_block=max_diff_chars,
        )
        rc, out, err = llm_runner(prompt=iter_prompt, options=list(options or []), timeout=timeout, model=model, workdir=workdir)
        last_rc, last_out, last_err = rc, out, err
        if rc != 0 and not out:
            return _finish("llm_failed", rc, out, err)

        message = parse_mutation_output(out)
        if message is None:
            invalid_count += 1
            report_iterations.append({"iteration": iteration, "kind": "invalid_output"})
            if invalid_count >= max_invalid:
                return _finish("invalid_output_limit_reached", rc, out, err)
            _add_evidence(
                {
                    "kind": "protocol_warning",
                    "iteration": iteration,
                    "warning": "previous_answer_was_not_valid_mutation_json",
                }
            )
            continue

        kind = str(message.get("kind"))
        iteration_row: dict[str, Any] = {"iteration": iteration, "kind": kind}
        report_iterations.append(iteration_row)

        if kind == KIND_FINAL_ANSWER:
            final_check = _hub_check(iteration_number=iteration)
            policy_status = str((final_check.get("policy_result") or {}).get("status") or "ok")
            source_line_status = str((final_check.get("source_line_policy_result") or {}).get("status") or "ok")
            answer = str(message.get("answer") or out)
            if policy_status != "ok" or source_line_status == "blocked":
                # ALWA-015: a final answer that violates the workspace
                # policy emits workspace_mutation_blocked. The
                # workspace_mutation_evaluated event already fired
                # inside _hub_check — this row is the *blocked* signal
                # for the audit chain.
                try:
                    from agent.common.audit import (
                        AUDIT_WORKSPACE_MUTATION_BLOCKED,
                        audit_workspace_mutation_event,
                    )
                    policy_dict = dict(final_check.get("policy_result") or {})
                    source_line_dict = dict(final_check.get("source_line_policy_result") or {})
                    blocked_changes = list(policy_dict.get("blocked_changes") or [])
                    violation_ids = [
                        f"{row.get('path','')}:{row.get('reason','')}"
                        for row in blocked_changes
                        if isinstance(row, dict)
                    ]
                    violation_ids.extend(
                        f"{row.get('path','')}:{row.get('reason_code','')}"
                        for row in list(source_line_dict.get("file_results") or [])
                        if isinstance(row, dict) and str(row.get("decision") or "") == "blocked"
                    )
                    violation_summary = (
                        "; ".join(
                            f"{row.get('path','')}:{row.get('reason','')}"
                            for row in blocked_changes
                            if isinstance(row, dict)
                        )
                        or None
                    )
                    audit_workspace_mutation_event(
                        AUDIT_WORKSPACE_MUTATION_BLOCKED,
                        task_id=task_id,
                        iteration_number=iteration,
                        mutation_mode=mode,
                        changed_paths=list(
                            (final_check.get("diff_result") or {}).get("changed_files") or []
                        ),
                        policy_decision=str(source_line_status if source_line_status == "blocked" else policy_status),
                        violation_ids=violation_ids,
                        violation_summary=violation_summary,
                        blocked_reason=str(source_line_status if source_line_status == "blocked" else policy_status),
                        source_line_policy_status=source_line_status,
                        source_line_policy_summary=dict(source_line_dict.get("summary") or {}),
                    )
                except Exception:
                    pass
                summary = {
                    "kind": "final_answer_blocked",
                    "status": "policy_blocked",
                    "answer": answer,
                    "policy_result": final_check.get("policy_result"),
                    "source_line_policy_result": final_check.get("source_line_policy_result"),
                }
                return _finish("policy_blocked", 0, json.dumps(summary, ensure_ascii=False), err)
            return _finish("final_answer", 0, answer, err)

        if kind in {KIND_NEEDS_APPROVAL, KIND_CANNOT_CONTINUE}:
            iteration_row["reason"] = str(message.get("reason") or "")
            summary = {"kind": kind, "reason": str(message.get("reason") or "")}
            if kind == KIND_NEEDS_APPROVAL:
                from agent.common.sgpt_tool_loop import register_pending_approval_request

                request_id = register_pending_approval_request(
                    task_id=task_id,
                    tool_name=str(message.get("tool_name") or "worker.needs_approval"),
                    arguments=dict(message.get("arguments") or {}),
                    reason=str(message.get("reason") or ""),
                )
                if request_id:
                    summary["approval_request_id"] = request_id
            return _finish(kind, 0, json.dumps(summary, ensure_ascii=False), err)

        if kind == KIND_WORKSPACE_WRITE:
            if mode != "controlled_workspace":
                iteration_row["result"] = "rejected_direct_write_in_strict_mode"
                _add_evidence(
                    build_tool_result(
                        tool_name="workspace_write",
                        tool_call_id=f"mutation:{iteration}",
                        status="policy_blocked",
                        risk_class="write",
                        error="direct_write_not_allowed_in_strict_patch_request",
                    )
                )
                continue
            applied: list[str] = []
            rejected: list[dict[str, str]] = []
            source_line_write_results: list[dict[str, Any]] = []
            for row in list(message.get("files") or []):
                rel = str((row or {}).get("path") or "").strip()
                content = (row or {}).get("content")
                attempts = file_attempts.get(rel, 0) + 1
                file_attempts[rel] = attempts
                if attempts > max_attempts_per_file:
                    rejected.append({"path": rel, "reason": "max_patch_attempts_per_file_exceeded"})
                    continue
                if not isinstance(content, str):
                    rejected.append({"path": rel, "reason": "content_must_be_text"})
                    continue
                forbidden = mutation_policy._is_forbidden(rel)
                if forbidden:
                    rejected.append({"path": rel, "reason": forbidden})
                    continue
                try:
                    target = resolve_workspace_path(workspace, rel)
                except WorkspacePathError as exc:
                    rejected.append({"path": rel, "reason": str(exc)})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                existed_before = target.exists()
                original_text = target.read_text(encoding="utf-8", errors="replace") if existed_before and target.is_file() else None
                baseline = {rel: len(original_text.splitlines()) if original_text is not None else None}
                target.write_text(content, encoding="utf-8")
                source_line_result = _gen_source_line_policy.evaluate_changed_files(
                    workspace_dir=workspace,
                    changed_rel_paths=[rel],
                    cfg=extract_policy_config(cfg),
                    baseline=baseline,
                    context={"task_id": task_id},
                ).as_dict()
                source_line_write_results.append(source_line_result)
                if source_line_result.get("status") == DECISION_BLOCKED:
                    if existed_before and original_text is not None:
                        target.write_text(original_text, encoding="utf-8")
                    else:
                        try:
                            target.unlink()
                        except FileNotFoundError:
                            pass
                    rejected.append({"path": rel, "reason": "source_line_policy_blocked"})
                    continue
                applied.append(rel)
            if any(row["reason"] == "max_patch_attempts_per_file_exceeded" for row in rejected) and not applied:
                iteration_row["result"] = "max_patch_attempts_per_file"
                summary = {"kind": "loop_aborted", "reason": "max_patch_attempts_per_file", "rejected": rejected}
                return _finish("max_patch_attempts_per_file", 0, json.dumps(summary, ensure_ascii=False), err)
            check = _hub_check(iteration_number=iteration)
            check["write_result"] = {"applied": applied, "rejected": rejected}
            if source_line_write_results:
                check["write_source_line_policy_results"] = source_line_write_results
            iteration_row["applied"] = applied
            iteration_row["rejected"] = rejected
            iteration_row["source_line_policy_statuses"] = [
                str(row.get("status") or "") for row in source_line_write_results
            ]
            _add_evidence(check)
            signature = _changes_signature(workspace, list((check.get("diff_result") or {}).get("changed_files") or []))
            if signature == last_change_signature:
                repeated_signature_count += 1
            else:
                repeated_signature_count = 0
            last_change_signature = signature
            if repeated_signature_count >= 1 and not applied:
                summary = {"kind": "loop_aborted", "reason": "no_progress_detected"}
                return _finish("no_progress_detected", 0, json.dumps(summary, ensure_ascii=False), err)
            if repeated_signature_count >= 2:
                summary = {"kind": "loop_aborted", "reason": "no_progress_detected"}
                return _finish("no_progress_detected", 0, json.dumps(summary, ensure_ascii=False), err)
            continue

        if kind == KIND_PATCH_REQUEST:
            tool_call_count += 1
            tool_call_id = f"patch_result:{tool_call_count}"
            variant = str(message.get("variant") or "unified_diff").strip().lower()
            rel = str(message.get("target_path") or "").strip()
            attempts = file_attempts.get(rel, 0) + 1
            file_attempts[rel] = attempts
            if attempts > max_attempts_per_file:
                summary = {"kind": "loop_aborted", "reason": "max_patch_attempts_per_file", "path": rel}
                return _finish("max_patch_attempts_per_file", 0, json.dumps(summary, ensure_ascii=False), err)
            if variant == "unified_diff":
                tool_name = "repo.apply_patch"
                arguments = {
                    "target_path": rel,
                    "variant": "unified_diff",
                    "unified_diff": str(message.get("unified_diff") or ""),
                    "expected_old_hash": str(message.get("expected_old_hash") or ""),
                    "reason": str(message.get("reason") or ""),
                }
            elif variant == "replace_range":
                tool_name = "repo.apply_patch"
                arguments = {
                    "target_path": rel,
                    "variant": "replace_range",
                    "line_start": int(message.get("line_start") or 0),
                    "line_end": int(message.get("line_end") or 0),
                    "replacement": str(message.get("replacement") if message.get("replacement") is not None else message.get("content") or ""),
                    "expected_old_hash": str(message.get("expected_old_hash") or ""),
                    "reason": str(message.get("reason") or ""),
                }
            else:
                tool_name = "repo.write_file"
                arguments = {
                    "path": rel,
                    "content": str(message.get("content") or ""),
                    "mode": "create_only" if variant == "write_file_create_only" else "replace_existing",
                    "expected_old_hash": str(message.get("expected_old_hash") or ""),
                }
            decision = tool_policy.evaluate(
                tool_name=tool_name,
                arguments=arguments,
                allowed_tools=None,
                mutation_mode=mode,
                task_id=task_id,
            )
            iteration_row["tool_name"] = tool_name
            iteration_row["policy_decision"] = decision.decision
            if not decision.allowed:
                _add_evidence(
                    build_tool_result(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        status=decision.decision,
                        risk_class=decision.risk_class,
                        error=decision.reason,
                        policy_decision=decision.as_dict(),
                    )
                )
                if decision.decision == "approval_required":
                    from agent.common.sgpt_tool_loop import register_pending_approval_request

                    request_id = register_pending_approval_request(
                        task_id=task_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        risk_class=decision.risk_class,
                        reason=decision.reason,
                    )
                    summary = {"kind": "loop_aborted", "reason": "approval_required", "tool_name": tool_name}
                    if request_id:
                        summary["approval_request_id"] = request_id
                    return _finish("approval_required", 0, json.dumps(summary, ensure_ascii=False), err)
                continue
            result = execute_ananta_tool(
                tool_name=tool_name,
                arguments=arguments,
                workspace_dir=str(workspace),
                tool_call_id=tool_call_id,
                config=cfg,
            )
            check = _hub_check(iteration_number=iteration)
            check["patch_result"] = result
            _add_evidence(check)
            iteration_row["patch_status"] = str(result.get("status") or "")
            continue

        if kind == KIND_TOOL_REQUEST:
            tool_call_count += 1
            tool_call_id = f"tool_result:{tool_call_count}"
            tool_name = str(message.get("tool_name") or "").strip()
            arguments = dict(message.get("arguments") or {})
            decision = tool_policy.evaluate(
                tool_name=tool_name,
                arguments=arguments,
                allowed_tools=None,
                mutation_mode=mode,
                task_id=task_id,
            )
            iteration_row["tool_name"] = tool_name
            iteration_row["policy_decision"] = decision.decision
            if not decision.allowed:
                blocked_result = build_tool_result(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status=decision.decision,
                    risk_class=decision.risk_class,
                    error=decision.reason,
                    policy_decision=decision.as_dict(),
                )
                if decision.decision == "approval_required":
                    from agent.common.sgpt_tool_loop import register_pending_approval_request

                    request_id = register_pending_approval_request(
                        task_id=task_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        risk_class=decision.risk_class,
                        reason=decision.reason,
                    )
                    if request_id:
                        blocked_result["approval_request_id"] = request_id
                _add_evidence(blocked_result)
                continue
            tool_cfg = {**cfg, "materialization_manifest": materialization_manifest}
            result = execute_ananta_tool(
                tool_name=tool_name,
                arguments=arguments,
                workspace_dir=str(workspace),
                tool_call_id=tool_call_id,
                config=tool_cfg,
            )
            if tool_name == "codecompass.plan_context":
                refs = list(((result.get("data") or {}).get("context_bundle") or {}).get("location_refs") or [])
                materialized = []
                for ref_index, ref in enumerate(refs[:4], start=1):
                    path = str((ref or {}).get("path") or "").strip()
                    if not path:
                        continue
                    range_result = execute_ananta_tool(
                        tool_name="repo.read_file_range",
                        arguments={
                            "path": path,
                            "line_start": int((ref or {}).get("line_start") or 1),
                            "line_end": int((ref or {}).get("line_end") or 1),
                        },
                        workspace_dir=str(workspace),
                        tool_call_id=f"{tool_call_id}:range:{ref_index}",
                        config=tool_cfg,
                    )
                    materialized.append(range_result)
                if materialized:
                    result["data"] = {**dict(result.get("data") or {}), "materialized_range_results": materialized}
            if tool_name == "test.run":
                check = _hub_check(
                    iteration_number=iteration,
                    ran_tests_result=dict(result.get("data") or {}),
                )
                check["tool_result"] = result
                _add_evidence(check)
            else:
                _add_evidence(result)
            iteration_row["tool_status"] = str(result.get("status") or "")
            continue

    summary = {
        "kind": "loop_aborted",
        "reason": "max_iterations",
        "last_output": last_out[:2000],
        "last_policy_result": last_policy_result,
    }
    return _finish("max_iterations", 0, json.dumps(summary, ensure_ascii=False), last_err)
