"""Workspace execution path for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as sub-split 001o.
Owns workspace locking, context delivery, local/native execution, snapshot diffing,
artifact sync, git push, verification, and response finalisation.

Backwards compatibility preserved via delegating wrapper in
:class:`TaskScopedExecutionService` (12-month deprecation window).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Callable

from flask import current_app

from agent.pipeline_trace import new_pipeline_trace, append_stage
from agent.runtime_policy import build_trace_record
from agent.services.native_worker_runtime_service import get_native_worker_runtime_service
from agent.services.service_registry import get_core_services
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.services.task_runtime_service import apply_artifact_first_completion, update_local_task_status
from agent.utils import _log_terminal_entry

from agent.services._task_scoped_citation import (
    build_flow_metrics_payload,
    extract_grounded_answer_payload,
)
from agent.services._task_scoped_config_policy import should_use_native_worker_runtime
from agent.services._task_scoped_repair import is_shell_meta_blocked_failure, is_command_not_found_failure

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

# Must import from the service module for backwards-compat identity (other code references
# _build_workspace_state_sync_record via this module).
from agent.services.task_scoped_execution_service import _build_workspace_state_sync_record

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def run_execute_workspace_path(
    *,
    tid: str,
    task: dict,
    command: str | None,
    tool_calls: list | None,
    reason: str,
    used_last_proposal: bool,
    task_kind: str,
    proposal_meta: dict,
    worker_profile: str | None,
    profile_source: str,
    policy_classification_summary: str | None,
    agent_cfg: dict,
    execution_policy,
    cli_runner: Callable | None,
    tool_definitions_resolver: Callable | None,
    rewrite_runtime_command_for_workspace_tools: Callable,
    attempt_repaired_execute_after_meta_block: Callable,
    register_goal_artifact_outputs: Callable,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.metrics import TASK_COMPLETED, TASK_FAILED

    exec_started_at = time.time()
    workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
    lock_ok, lock_reason = get_worker_workspace_service().acquire_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)
    if not lock_ok:
        return TaskScopedRouteResponse(
            data={"status": "blocked", "reason_code": lock_reason or "workspace_write_conflict", "task_id": tid},
            status="blocked",
            message="Shared output directory is currently locked",
            code=409,
        )

    context_delivery_result = None
    if workspace_ctx.context_policy is not None and getattr(workspace_ctx.context_policy, "scope_mode", "full") != "full":
        try:
            from agent.services.context_delivery_service import get_context_delivery_service
            context_delivery_result = get_context_delivery_service().deliver(task=task, workspace_ctx=workspace_ctx)
        except Exception as _csd_err:
            return TaskScopedRouteResponse(
                data={"status": "failed", "error": "context_delivery_failed", "detail": str(_csd_err), "task_id": tid},
                status="failed",
                message="Context delivery failed",
                code=500,
            )

    try:
        before_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
        command, runtime_command_rewrite = rewrite_runtime_command_for_workspace_tools(
            command=command,
            workspace_dir=str(workspace_ctx.workspace_dir),
        )
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind"),
            policy_version=((task.get("last_proposal", {}) or {}).get("trace") or {}).get("policy_version"),
            metadata={"task_id": tid},
        )
        native_artifact_refs: list[dict] = []
        execution_repair_meta: dict | None = None
        if should_use_native_worker_runtime(proposal_meta=proposal_meta, agent_cfg=agent_cfg, command=command):
            append_stage(
                pipeline,
                name="native_worker_execute",
                status="ok",
                metadata={"runtime_path": "native_worker_pipeline"},
            )
            native_execution = get_native_worker_runtime_service().execute_and_verify_command(
                tid=tid,
                task=task,
                command=str(command or ""),
                trace_id=str(((proposal_meta.get("trace") or {}).get("trace_id") or f"native-exec-{tid}")),
                worker_profile=worker_profile,
                profile_source=profile_source,
                timeout_seconds=int(execution_policy.timeout_seconds),
                workspace_dir=workspace_ctx.workspace_dir,
                native_runtime_payload=(proposal_meta.get("worker_context", {}).get("native_runtime") if isinstance(proposal_meta.get("worker_context", {}).get("native_runtime"), dict) else {}),
                agent_cfg=agent_cfg,
            )
            from agent.services.task_scoped_execution_service import LocalExecutionResult
            execution_run = LocalExecutionResult(
                output=str(native_execution.get("output") or ""),
                exit_code=int(native_execution.get("exit_code") or 1),
                retries_used=0,
                failure_type=str(native_execution.get("failure_type") or "native_worker_runtime"),
                retry_history=[],
                status=str(native_execution.get("status") or "failed"),
                loop_signals=[],
                loop_detection=None,
                approval_decision=dict(native_execution.get("approval_decision") or {}),
            )
            native_artifact_refs = [ref for ref in list(native_execution.get("artifact_refs") or []) if isinstance(ref, dict)]
            execution_repair_meta = {
                "native_worker_runtime": dict(native_execution.get("native_runtime") or {}),
                "runtime_path": "native_worker_pipeline",
            }
            native_policy_summary = str(native_execution.get("policy_classification_summary") or "").strip().lower() or None
            if native_policy_summary:
                policy_classification_summary = native_policy_summary
        else:
            execution_run = get_core_services().task_execution_service.execute_local_step(
                tid=tid,
                task=task,
                command=command,
                tool_calls=tool_calls,
                execution_policy=execution_policy,
                guard_cfg=agent_cfg,
                pipeline=pipeline,
                exec_started_at=exec_started_at,
                working_directory=str(workspace_ctx.workspace_dir),
            )
            if used_last_proposal and cli_runner and (
                is_shell_meta_blocked_failure(execution_run.output, execution_run.failure_type)
                or is_command_not_found_failure(execution_run.output, execution_run.failure_type)
            ):
                repaired_execution = attempt_repaired_execute_after_meta_block(
                    tid=tid,
                    task=task,
                    task_kind=task_kind,
                    command=command,
                    execution_output=execution_run.output,
                    execution_policy=execution_policy,
                    agent_cfg=agent_cfg,
                    cli_runner=cli_runner,
                    tool_definitions_resolver=tool_definitions_resolver,
                    pipeline=pipeline,
                    workspace_dir=str(workspace_ctx.workspace_dir),
                    exec_started_at=exec_started_at,
                )
                if repaired_execution:
                    command = repaired_execution["command"]
                    tool_calls = repaired_execution["tool_calls"]
                    reason = repaired_execution["reason"]
                    execution_run = repaired_execution["execution_run"]
                    execution_repair_meta = repaired_execution["repair_meta"]

        after_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
        changed_files = get_worker_workspace_service().detect_changed_files(before_workspace_snapshot, after_workspace_snapshot)
        meaningful_changed_files = get_worker_workspace_service().filter_meaningful_changed_files(changed_files)
        from worker.core.file_change_set import diff_snapshots
        from pathlib import Path
        before_id = hashlib.sha256(str(sorted(before_workspace_snapshot.items())).encode()).hexdigest()[:16]
        after_id = hashlib.sha256(str(sorted(after_workspace_snapshot.items())).encode()).hexdigest()[:16]
        exec_id = f"exec-{tid}-{int(time.time()*1000)}"
        fcs = diff_snapshots(
            task_id=tid,
            execution_id=exec_id,
            workspace_root=Path(workspace_ctx.workspace_dir),
            before_snapshot_id=before_id,
            before_snapshot=before_workspace_snapshot,
            after_snapshot_id=after_id,
            after_snapshot=after_workspace_snapshot,
        )
        git_pushed: bool = False
        git_ctx = getattr(workspace_ctx, "git_context", None)
        if git_ctx is not None and getattr(git_ctx, "is_clone", False) and meaningful_changed_files:
            try:
                from agent.services.workspace_git_service import get_workspace_git_service
                git_pushed = bool(get_workspace_git_service().commit_and_push(
                    git_ctx.workspace_dir,
                    branch=git_ctx.branch,
                    message=f"task {str(tid)[:12]}: {str(task.get('title') or tid)[:60]}",
                ))
            except Exception as _git_push_err:
                logging.warning("git commit+push failed for task %s: %s", tid, _git_push_err)

        workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        combined_artifact_refs = [*list(workspace_artifact_refs or []), *list(native_artifact_refs or [])]
        execution_duration_ms = int((time.time() - exec_started_at) * 1000)
        tool_run_refs: list[dict] = []
        try:
            from agent.services.tool_run_catalog_service import get_tool_run_catalog_service
            run_entry = get_tool_run_catalog_service().build_run_entry(
                task_id=str(tid),
                index=1,
                tool_name="shell",
                command=str(command or ""),
                exit_code=int(execution_run.exit_code),
                stdout=str(execution_run.output or ""),
                stderr="",
                artifact_paths=[
                    str(item.get("path") or item.get("artifact_path") or "")
                    for item in list(combined_artifact_refs or [])
                    if isinstance(item, dict)
                ],
                started_at=exec_started_at,
                ended_at=time.time(),
            )
            tool_run_refs = [run_entry]
        except Exception:
            tool_run_refs = []

        proposal_meta = task.get("last_proposal", {}) or {}
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            backend=proposal_meta.get("backend"),
            requested_backend=proposal_meta.get("backend"),
            routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={
                "retries_used": execution_run.retries_used,
                "duration_ms": execution_duration_ms,
                "failure_type": execution_run.failure_type,
            },
        )
        if execution_run.status == "completed":
            TASK_COMPLETED.inc()
        else:
            TASK_FAILED.inc()

        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=execution_run.status,
            reason=reason,
            command=command,
            tool_calls=tool_calls,
            output=execution_run.output,
            exit_code=execution_run.exit_code,
            retries_used=execution_run.retries_used,
            retry_history=execution_run.retry_history,
            failure_type=execution_run.failure_type,
            execution_duration_ms=execution_duration_ms,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            artifact_refs=combined_artifact_refs or None,
            extra_history={
                "workspace_changed_files": changed_files,
                "workspace_meaningful_changed_files": meaningful_changed_files,
                "file_change_set": fcs.to_dict(),
                "workspace_dir": str(workspace_ctx.workspace_dir),
                "workspace_artifact_count": len(workspace_artifact_refs),
                "native_artifact_count": len(native_artifact_refs),
                "workspace_state_sync": _build_workspace_state_sync_record(
                    task=task,
                    materialization_manifest=workspace_ctx.materialization_manifest,
                    workspace_artifact_refs=workspace_artifact_refs,
                    git_pushed=git_pushed,
                ),
                "loop_signals": execution_run.loop_signals,
                "loop_detection": execution_run.loop_detection,
                "approval_decision": execution_run.approval_decision,
                "execution_repair": execution_repair_meta,
                "tool_run_refs": tool_run_refs,
                "runtime_command_rewrite": runtime_command_rewrite,
                "flow_metrics": build_flow_metrics_payload(
                    run_id=str((((task.get("last_proposal") or {}).get("trace") or {}).get("trace_id") or "")),
                    phase="execute",
                    propose_ok=True,
                    execute_ok=execution_run.status == "completed",
                    artifact_created=bool(meaningful_changed_files),
                    worker_profile=worker_profile,
                    profile_source=profile_source,
                    policy_classification=policy_classification_summary,
                ),
            },
        )
        if execution_run.status == "completed":
            worker_execution_contract = dict(task.get("worker_execution_contract") or {})
            expected_paths = [
                str(item.get("relative_path") or "").strip()
                for item in list(worker_execution_contract.get("expected_artifacts") or [])
                if isinstance(item, dict) and bool(item.get("required", True)) and str(item.get("relative_path") or "").strip()
            ]
            artifact_ids = [str(ref.get("artifact_id") or "").strip() for ref in list(combined_artifact_refs or []) if str(ref.get("artifact_id") or "").strip()]
            produced_paths = {
                str(ref.get("workspace_relative_path") or "").strip()
                for ref in list(combined_artifact_refs or [])
                if isinstance(ref, dict) and str(ref.get("workspace_relative_path") or "").strip()
            }
            missing = [path for path in expected_paths if path not in produced_paths]
            collection_result = {
                "manifest_valid": not missing,
                "artifact_ids": artifact_ids,
                "manifest_id": f"manifest-{tid}",
                "missing_expected_paths": missing,
            }
            final_status = apply_artifact_first_completion(
                tid,
                collection_result=collection_result,
                advisory_parse_result=None,
                exit_code=execution_run.exit_code,
                retry_count=int(execution_run.retries_used or 0),
                expected_paths=expected_paths,
                verification_required=bool(expected_paths),
                allow_synthesized_manifest=False,
            )
            response_payload["status"] = final_status
            response_payload["artifact_completion"] = {
                "expected_paths": expected_paths,
                "produced_paths": sorted(produced_paths),
                "missing_expected_paths": missing,
                "final_status": final_status,
            }
            goal_output_artifacts = register_goal_artifact_outputs(
                task=task,
                tid=tid,
                artifact_refs=list(combined_artifact_refs or []),
            )
            if goal_output_artifacts:
                response_payload["goal_output_artifacts"] = goal_output_artifacts

        verification_status = dict((task.get("verification_status") or {}))
        source_catalog_status = dict(verification_status.get("source_catalog") or {})
        source_catalog_sources = list(source_catalog_status.get("sources") or [])
        answer_payload = extract_grounded_answer_payload(response_payload.get("output"))
        answer_verification = dict(verification_status.get("answer_verification") or {})
        answer_verification.setdefault("answer_schema", "grounded_answer.v1")
        if answer_payload and source_catalog_sources:
            from agent.services.citation_verification_service import get_citation_verification_service
            verification_result = get_citation_verification_service().verify(
                task_id=str(tid),
                answer_payload=answer_payload,
                source_catalog={
                    "schema": "source_catalog.v1",
                    "catalog_id": source_catalog_status.get("source_catalog_id"),
                    "task_id": str(tid),
                    "retrieval_trace_id": source_catalog_status.get("retrieval_trace_id"),
                    "retrieval_context_hash": "",
                    "retrieval_manifest_hash": "",
                    "catalog_hash": source_catalog_status.get("source_catalog_hash") or "0" * 16,
                    "sources": source_catalog_sources,
                },
                tool_run_catalog=tool_run_refs,
            )
            answer_verification.update(
                {
                    "citation_verification_status": verification_result.get("status"),
                    "verified_claim_count": int(verification_result.get("verified_claim_count") or 0),
                    "unverified_claim_count": int(verification_result.get("unverified_claim_count") or 0),
                    "failed_claims": list(verification_result.get("failed_claims") or []),
                    "tool_run_refs": tool_run_refs,
                }
            )
            if verification_result.get("status") != "verified" and str(response_payload.get("status") or "") == "completed":
                response_payload["status"] = "failed"
        else:
            answer_verification.setdefault("citation_verification_status", "not_evaluated")
            answer_verification.setdefault("verified_claim_count", 0)
            answer_verification.setdefault("unverified_claim_count", 0)
            answer_verification.setdefault("failed_claims", [])

        verification_status["answer_verification"] = answer_verification
        update_local_task_status(
            tid,
            str(response_payload.get("status") or execution_run.status),
            verification_status=verification_status,
        )

        history_len = len(task.get("history", []) or [])
        _log_terminal_entry(current_app.config["AGENT_NAME"], history_len, "out", command=command, task_id=tid)
        _log_terminal_entry(
            current_app.config["AGENT_NAME"],
            history_len,
            "in",
            output=execution_run.output,
            exit_code=execution_run.exit_code,
            task_id=tid,
        )
        return TaskScopedRouteResponse(data=response_payload)
    finally:
        get_worker_workspace_service().release_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)
