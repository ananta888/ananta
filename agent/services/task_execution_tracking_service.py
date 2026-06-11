from __future__ import annotations

import time

from flask import current_app

from agent.services.ingestion_service import get_ingestion_service
from agent.services.repository_registry import get_repository_registry
from agent.services.result_memory_service import get_result_memory_service
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.task_runtime_service import update_local_task_status
from agent.services.worker_job_service import get_worker_job_service
from agent.services.task_execution_metrics import (
    build_control_layer_observability_snapshot as _build_control_layer_observability_snapshot,
    build_execution_cost_summary as _build_execution_cost_summary,
    record_benchmark_sample as _record_benchmark_sample,
)
from agent.services._task_execution_tracking_artifact_flow import (
    build_artifact_flow_read_model as _build_artifact_flow_read_model,
)

_TASK_TERMINAL_STATUSES = {"completed", "failed"}
_JOB_ACTIVE_STATUSES = {"created", "delegated", "assigned", "in_progress", "running"}
_JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked"}


class TaskExecutionTrackingService:
    """Centralizes proposal persistence and execution tracking for task execution routes."""

    def build_artifact_flow_read_model(self, *, overrides: dict | None = None) -> dict:
        return _build_artifact_flow_read_model(overrides=overrides)

    def _reconciliation_threshold_seconds(self) -> int:
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        try:
            value = int(cfg.get("agent_offline_timeout") or 300)
        except (TypeError, ValueError):
            value = 300
        return max(60, value)

    def _build_execution_reconciliation_issue(
        self,
        *,
        task: dict,
        job,
        now: float,
        stale_after_seconds: int,
        offline_worker_urls: set[str],
    ) -> dict | None:
        task_status = str(task.get("status") or "").strip().lower()
        worker_job_id = str(task.get("current_worker_job_id") or "").strip()
        if not worker_job_id:
            return None

        if job is None:
            if task_status not in _TASK_TERMINAL_STATUSES:
                return {
                    "issue_code": "missing_worker_job",
                    "task_id": task.get("id"),
                    "task_status": task_status,
                    "worker_job_id": worker_job_id,
                    "worker_url": str(task.get("assigned_agent_url") or "").strip() or None,
                    "recommended_task_status": "blocked",
                    "recommended_worker_job_status": None,
                    "age_seconds": None,
                    "details": {"reason": "current_worker_job_missing"},
                }
            return None

        job_status = str(job.status or "").strip().lower()
        worker_url = str(job.worker_url or "").strip() or None
        age_seconds = max(0, int(now - max(float(job.updated_at or 0), float(task.get("updated_at") or 0))))

        if task_status in _TASK_TERMINAL_STATUSES and job_status not in _JOB_TERMINAL_STATUSES:
            return {
                "issue_code": "terminal_job_mismatch",
                "task_id": task.get("id"),
                "task_status": task_status,
                "worker_job_id": worker_job_id,
                "worker_job_status": job_status,
                "worker_url": worker_url,
                "recommended_task_status": None,
                "recommended_worker_job_status": task_status,
                "age_seconds": age_seconds,
                "details": {"reason": "terminal_task_with_nonterminal_worker_job"},
            }

        if task_status not in _TASK_TERMINAL_STATUSES and job_status in {"completed", "failed"}:
            return {
                "issue_code": "task_status_lags_worker_job",
                "task_id": task.get("id"),
                "task_status": task_status,
                "worker_job_id": worker_job_id,
                "worker_job_status": job_status,
                "worker_url": worker_url,
                "recommended_task_status": job_status,
                "recommended_worker_job_status": None,
                "age_seconds": age_seconds,
                "details": {"reason": "worker_job_terminal_but_task_active"},
            }

        if task_status not in _TASK_TERMINAL_STATUSES and job_status in _JOB_ACTIVE_STATUSES and age_seconds >= stale_after_seconds:
            return {
                "issue_code": "stuck_execution",
                "task_id": task.get("id"),
                "task_status": task_status,
                "worker_job_id": worker_job_id,
                "worker_job_status": job_status,
                "worker_url": worker_url,
                "recommended_task_status": "blocked",
                "recommended_worker_job_status": None,
                "age_seconds": age_seconds,
                "details": {
                    "reason": "worker_job_stale",
                    "worker_offline": bool(worker_url and worker_url in offline_worker_urls),
                    "stale_after_seconds": stale_after_seconds,
                },
            }
        return None

    def build_execution_reconciliation_snapshot(self, *, limit: int = 20, now: float | None = None) -> dict:
        repos = get_repository_registry()
        task_items = [task.model_dump() for task in repos.task_repo.get_all()]
        agent_items = repos.agent_repo.get_all()
        job_repo = repos.worker_job_repo
        current_time = float(now or time.time())
        stale_after_seconds = self._reconciliation_threshold_seconds()
        offline_worker_urls = {
            str(agent.url or "").strip()
            for agent in agent_items
            if str(agent.status or "").strip().lower() == "offline" and str(agent.url or "").strip()
        }

        issues: list[dict] = []
        counts = {"missing_worker_job": 0, "terminal_job_mismatch": 0, "task_status_lags_worker_job": 0, "stuck_execution": 0}
        for task in task_items:
            worker_job_id = str(task.get("current_worker_job_id") or "").strip()
            if not worker_job_id:
                continue
            job = job_repo.get_by_id(worker_job_id)
            issue = self._build_execution_reconciliation_issue(
                task=task,
                job=job,
                now=current_time,
                stale_after_seconds=stale_after_seconds,
                offline_worker_urls=offline_worker_urls,
            )
            if not issue:
                continue
            counts[issue["issue_code"]] = counts.get(issue["issue_code"], 0) + 1
            issues.append(issue)

        issues.sort(key=lambda item: int(item.get("age_seconds") or 0), reverse=True)
        return {
            "status": "degraded" if issues else "ok",
            "stale_after_seconds": stale_after_seconds,
            "issue_counts": counts,
            "affected_count": len(issues),
            "affected_tasks": issues[:limit],
        }

    def build_control_layer_observability_snapshot(self, *, max_tasks: int = 80) -> dict:
        return _build_control_layer_observability_snapshot(max_tasks=max_tasks)

    def reconcile_worker_executions(self, *, now: float | None = None, limit: int = 50) -> dict:
        repos = get_repository_registry()
        snapshot = self.build_execution_reconciliation_snapshot(limit=limit, now=now)
        if not snapshot.get("affected_tasks"):
            return snapshot

        decisions: list[dict] = []
        timestamp = float(now or time.time())
        for issue in list(snapshot.get("affected_tasks") or []):
            task = repos.task_repo.get_by_id(str(issue.get("task_id") or ""))
            if not task:
                continue
            issue_code = str(issue.get("issue_code") or "")
            verification_status = dict(task.verification_status or {})
            verification_status["execution_reconciliation"] = {
                "issue_code": issue_code,
                "worker_job_id": issue.get("worker_job_id"),
                "worker_job_status": issue.get("worker_job_status"),
                "recommended_task_status": issue.get("recommended_task_status"),
                "recommended_worker_job_status": issue.get("recommended_worker_job_status"),
                "age_seconds": issue.get("age_seconds"),
                "details": issue.get("details") or {},
                "reconciled_at": timestamp,
            }
            decision = {
                "issue_code": issue_code,
                "task_id": task.id,
                "worker_job_id": issue.get("worker_job_id"),
                "recommended_task_status": issue.get("recommended_task_status"),
                "recommended_worker_job_status": issue.get("recommended_worker_job_status"),
            }

            recommended_task_status = issue.get("recommended_task_status")
            if recommended_task_status and str(task.status or "").strip().lower() != str(recommended_task_status):
                update_local_task_status(
                    task.id,
                    str(recommended_task_status),
                    verification_status=verification_status,
                    event_type="task_reconciled",
                    event_actor="hub",
                    event_details=decision,
                )
                decision["task_status_updated"] = True
            elif issue_code == "missing_worker_job":
                update_local_task_status(
                    task.id,
                    str(task.status or "blocked"),
                    verification_status=verification_status,
                    event_type="task_reconciled",
                    event_actor="hub",
                    event_details=decision,
                )
                decision["task_status_updated"] = False

            recommended_worker_job_status = issue.get("recommended_worker_job_status")
            if recommended_worker_job_status:
                job = repos.worker_job_repo.get_by_id(str(issue.get("worker_job_id") or ""))
                if job and str(job.status or "").strip().lower() != str(recommended_worker_job_status):
                    job.status = str(recommended_worker_job_status)
                    job.updated_at = timestamp
                    metadata = dict(job.job_metadata or {})
                    metadata["execution_reconciliation"] = {
                        "issue_code": issue_code,
                        "reconciled_at": timestamp,
                        "aligned_to_task_status": str(recommended_worker_job_status),
                    }
                    job.job_metadata = metadata
                    repos.worker_job_repo.save(job)
                    decision["worker_job_status_updated"] = True

            decisions.append(decision)

        snapshot["decisions"] = decisions
        snapshot["status"] = "ok" if decisions else snapshot.get("status", "ok")
        return snapshot

    def persist_proposal_result(
        self,
        *,
        tid: str,
        task: dict | None,
        proposal: dict,
        history_event: dict | None = None,
    ) -> dict:
        # Keep telemetry structurally present for downstream diagnostics even
        # when worker/orchestrator did not return a cli_result payload.
        cli_result = proposal.get("cli_result")
        cfg = (current_app.config.get("AGENT_CONFIG", {}) or {})
        llm_profile_policy = dict(cfg.get("llm_profile_policy") or {})
        allow_synthetic_fallback = bool(llm_profile_policy.get("allow_synthetic_fallback", False))
        if not isinstance(cli_result, dict) and allow_synthetic_fallback:
            backend = str(proposal.get("backend") or "orchestrator").strip() or "orchestrator"
            model = str(proposal.get("model") or "").strip() or None
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": backend,
                "llm_call_profile": [
                    {
                        "name": "propose_persist_fallback",
                        "backend": backend,
                        "provider": None,
                        "model": model,
                        "success": True,
                        "latency_ms": None,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "source": "orchestrator_synthetic",
                        "estimated": True,
                        "error_type": None,
                        "error_message": None,
                        "started_at": None,
                        "ended_at": None,
                    }
                ],
            }
            proposal["cli_result"] = cli_result
        elif not isinstance(cli_result, dict):
            proposal["cli_result"] = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": str(proposal.get("backend") or "orchestrator").strip() or "orchestrator",
            }
        history = list((task or {}).get("history") or [])
        verification_status = dict((task or {}).get("verification_status") or {})
        flow_metrics = {}
        if isinstance(proposal.get("flow_metrics"), dict):
            flow_metrics = dict(proposal.get("flow_metrics") or {})
        if isinstance(history_event, dict) and isinstance(history_event.get("flow_metrics"), dict):
            flow_metrics = {**flow_metrics, **dict(history_event.get("flow_metrics") or {})}
        if flow_metrics:
            merged_metrics = dict(verification_status.get("task_flow_metrics") or {})
            merged_metrics.update(flow_metrics)
            merged_metrics["updated_at"] = time.time()
            verification_status["task_flow_metrics"] = merged_metrics
        routing = dict(proposal.get("routing") or {})
        verification_status["llm_diagnostics"] = {
            "propose_backend": str(proposal.get("backend") or ""),
            "propose_model": str(proposal.get("model") or ""),
            "selected_strategy": str(((routing.get("propose_strategy_meta") or {}).get("selected_strategy") or "")),
            "effective_strategy_mode": str(((routing.get("propose_strategy_meta") or {}).get("effective_strategy_mode") or "")),
            "inference_provider": str(routing.get("inference_provider") or ""),
            "inference_model": str(routing.get("inference_model") or ""),
            "updated_at": time.time(),
        }
        selected_strategy = str(((proposal.get("routing") or {}).get("propose_strategy_meta") or {}).get("selected_strategy") or "").strip()
        trace_meta = dict(proposal.get("trace") or {}) if isinstance(proposal.get("trace"), dict) else {}
        trace_id = str(trace_meta.get("trace_id") or "").strip() or None
        flow_reason = str(proposal.get("reason") or "").strip()
        get_execution_audit_service().emit(
            operation_type="proposal_trace_tracking",
            outcome="recorded" if trace_id else "missing_trace_link",
            trace_id=trace_id,
            goal_id=(task or {}).get("goal_id"),
            task_id=tid,
            actor_role="hub",
            details={
                "selected_strategy": selected_strategy or None,
                "backend": str(proposal.get("backend") or "").strip() or None,
                "model": str(proposal.get("model") or "").strip() or None,
                "has_cli_profile": bool(
                    isinstance(proposal.get("cli_result"), dict)
                    and isinstance((proposal.get("cli_result") or {}).get("llm_call_profile"), list)
                    and len(list((proposal.get("cli_result") or {}).get("llm_call_profile") or [])) > 0
                ),
                "has_forwarded_request": bool(isinstance(proposal.get("forwarded_request"), dict)),
                "reason_preview": flow_reason[:160] if flow_reason else None,
            },
        )
        if selected_strategy:
            get_execution_audit_service().emit(
                operation_type="llm_strategy_selection",
                outcome="selected",
                trace_id=trace_id,
                goal_id=(task or {}).get("goal_id"),
                task_id=tid,
                actor_role="hub",
                details={
                    "selected_strategy": selected_strategy,
                    "effective_strategy_mode": (((proposal.get("routing") or {}).get("propose_strategy_meta") or {}).get("effective_strategy_mode")),
                    "selection_reason": "policy_orchestrator_selection",
                },
            )

        if history_event:
            event = dict(history_event)
            event.setdefault("timestamp", time.time())
            history.append(event)
            update_local_task_status(
                tid,
                "proposing",
                last_proposal=proposal,
                history=history,
                verification_status=verification_status,
            )
            return proposal
        update_local_task_status(
            tid,
            "proposing",
            last_proposal=proposal,
            verification_status=verification_status,
        )
        return proposal

    def persist_research_artifact(self, *, tid: str, task: dict | None, research_artifact: dict | None) -> dict | None:
        if not isinstance(research_artifact, dict):
            return None
        report_markdown = str(research_artifact.get("report_markdown") or "").strip()
        if not report_markdown:
            return None
        artifact, version, _ = get_ingestion_service().upload_artifact(
            filename=f"{tid or 'task'}-research-report.md",
            content=report_markdown.encode("utf-8"),
            created_by=str((task or {}).get("assigned_agent_url") or current_app.config.get("AGENT_NAME") or "system"),
            media_type="text/markdown",
            collection_name="task-execution-results",
        )
        _, _, document = get_ingestion_service().extract_artifact(artifact.id)
        citations = list(research_artifact.get("citations") or [])
        sources = list(research_artifact.get("sources") or [])
        return {
            "kind": research_artifact.get("kind") or "research_report",
            "artifact_id": artifact.id,
            "artifact_version_id": version.id,
            "extracted_document_id": document.id if document else None,
            "filename": artifact.latest_filename,
            "media_type": artifact.latest_media_type,
            "task_id": tid,
            "worker_job_id": str((task or {}).get("current_worker_job_id") or "").strip() or None,
            "content_hash": version.sha256,
            "provenance_summary": {
                "artifact_type": research_artifact.get("kind") or "research_report",
                "source_count": len(sources),
                "has_citations": bool(citations),
                "citation_count": len(citations),
                "trace_bundle_ref": str(((research_artifact.get("trace") or {}).get("trace_bundle_id") or "")).strip() or None,
            },
        }

    def sync_worker_result_tracking(
        self,
        *,
        tid: str,
        task: dict | None,
        status: str,
        output: str,
        trace: dict,
        artifact_refs: list[dict] | None = None,
    ):
        task = task or {}
        worker_job_id = str(task.get("current_worker_job_id") or "").strip() or None
        if worker_job_id:
            get_worker_job_service().record_worker_result(
                worker_job_id=worker_job_id,
                task_id=tid,
                worker_url=str(current_app.config.get("AGENT_URL") or current_app.config.get("AGENT_NAME") or "local"),
                status=status,
                output=output,
                metadata={"trace_id": trace.get("trace_id"), "source": "task_execute"},
            )
        if not worker_job_id and not artifact_refs:
            return None
        return get_result_memory_service().record_worker_result_memory(
            task_id=tid,
            goal_id=task.get("goal_id"),
            trace_id=trace.get("trace_id") or task.get("goal_trace_id"),
            worker_job_id=worker_job_id,
            title=task.get("title") or task.get("description"),
            output=output,
            artifact_refs=list(artifact_refs or [{"kind": "task_output", "task_id": tid, "worker_job_id": worker_job_id}]),
            retrieval_tags=[
                value
                for value in [
                    str(task.get("task_kind") or "").strip(),
                    str(task.get("goal_id") or "").strip(),
                    str(status).strip(),
                ]
                if value
            ],
            metadata={"source": "task_execute"},
        )

    def build_execution_cost_summary(
        self,
        *,
        task: dict | None,
        proposal_meta: dict | None,
        output: str,
        tool_calls: list[dict] | None,
        execution_duration_ms: int,
    ) -> dict:
        return _build_execution_cost_summary(
            task=task,
            proposal_meta=proposal_meta,
            output=output,
            tool_calls=tool_calls,
            execution_duration_ms=execution_duration_ms,
        )

    def record_benchmark_sample(
        self,
        *,
        cost_summary: dict,
        success: bool,
        quality_gate_passed: bool,
        proposal_meta: dict | None = None,
    ) -> None:
        _record_benchmark_sample(
            cost_summary=cost_summary,
            success=success,
            quality_gate_passed=quality_gate_passed,
            proposal_meta=proposal_meta,
        )

    def finalize_execution_result(
        self,
        *,
        tid: str,
        task: dict | None,
        status: str,
        reason: str,
        command: str | None,
        tool_calls: list[dict] | None,
        output: str,
        exit_code: int | None,
        retries_used: int,
        retry_history: list[dict] | None,
        failure_type: str,
        execution_duration_ms: int,
        trace: dict,
        pipeline: dict,
        artifact_refs: list[dict] | None = None,
        extra_history: dict | None = None,
    ) -> dict:
        task = task or {}
        proposal_meta = dict(task.get("last_proposal") or {})
        history = list(task.get("history") or [])
        cost_summary = self.build_execution_cost_summary(
            task=task,
            proposal_meta=proposal_meta,
            output=output,
            tool_calls=tool_calls,
            execution_duration_ms=execution_duration_ms,
        )
        history_event = {
            "event_type": "execution_result",
            "prompt": task.get("description"),
            "reason": reason,
            "command": command,
            "tool_calls": tool_calls,
            "output": output,
            "exit_code": exit_code,
            "backend": proposal_meta.get("backend"),
            "execution_backend": ((proposal_meta.get("routing") or {}).get("execution_backend")) or proposal_meta.get("backend"),
            "inference_provider": ((proposal_meta.get("routing") or {}).get("inference_provider")) or cost_summary.get("inference_provider"),
            "inference_model": ((proposal_meta.get("routing") or {}).get("inference_model")) or cost_summary.get("inference_model"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "retries_used": retries_used,
            "retry_history": list(retry_history or []),
            "duration_ms": execution_duration_ms,
            "failure_type": failure_type,
            "pipeline": dict(pipeline or {}),
            "trace": trace,
            "timestamp": time.time(),
            "cost_summary": cost_summary,
            **dict(extra_history or {}),
        }
        history.append(history_event)
        scope_summary = self._derive_execution_scope_summary(history)
        provenance = self._derive_execution_provenance(history)
        memory_entry = self.sync_worker_result_tracking(
            tid=tid,
            task=task,
            status=status,
            output=output,
            trace=trace,
            artifact_refs=artifact_refs,
        )
        verification_status = dict(task.get("verification_status") or {})
        if scope_summary:
            verification_status["execution_scope"] = scope_summary
        if provenance:
            verification_status["execution_provenance"] = provenance
        approval_decision = dict((extra_history or {}).get("approval_decision") or {})
        if approval_decision:
            verification_status["approval_state"] = {
                "classification": approval_decision.get("classification"),
                "reason_code": approval_decision.get("reason_code"),
                "required_confirmation_level": approval_decision.get("required_confirmation_level"),
                "operation_class": approval_decision.get("operation_class"),
                "enforced": bool(approval_decision.get("enforced")),
                "updated_at": time.time(),
            }
        flow_metrics = dict((extra_history or {}).get("flow_metrics") or {})
        if flow_metrics:
            merged_metrics = dict(verification_status.get("task_flow_metrics") or {})
            merged_metrics.update(flow_metrics)
            merged_metrics["updated_at"] = time.time()
            verification_status["task_flow_metrics"] = merged_metrics
            verification_status["loop_telemetry"] = {
                "phase": flow_metrics.get("phase"),
                "propose_ok": flow_metrics.get("propose_ok"),
                "execute_ok": flow_metrics.get("execute_ok"),
                "artifact_created": flow_metrics.get("artifact_created"),
                "run_id": flow_metrics.get("run_id"),
                "updated_at": time.time(),
            }
        file_change_set = dict((extra_history or {}).get("file_change_set") or {})
        if file_change_set:
            verification_status["artifact_snapshot_diff"] = {
                "execution_id": file_change_set.get("execution_id"),
                "before_snapshot_id": file_change_set.get("before_snapshot_id"),
                "after_snapshot_id": file_change_set.get("after_snapshot_id"),
                "changed_count": len(list(file_change_set.get("changed_files") or [])),
                "added_count": len(list(file_change_set.get("added_files") or [])),
                "removed_count": len(list(file_change_set.get("removed_files") or [])),
                "updated_at": time.time(),
            }
        workspace_state_sync = dict((extra_history or {}).get("workspace_state_sync") or {})
        if workspace_state_sync:
            verification_status["workspace_state_sync"] = {
                "sync_mode": workspace_state_sync.get("sync_mode", "none"),
                "source_of_truth": workspace_state_sync.get("source_of_truth", "task_local"),
                "input_materialization": {
                    "artifact_count": len(list(workspace_state_sync.get("input_artifacts") or [])),
                    "artifacts": list(workspace_state_sync.get("input_artifacts") or []),
                },
                "output_publication": {
                    "artifact_count": len(list(workspace_state_sync.get("output_artifacts") or [])),
                    "artifacts": list(workspace_state_sync.get("output_artifacts") or []),
                    "git_pushed": bool(workspace_state_sync.get("git_pushed")),
                },
                "updated_at": time.time(),
            }
        verification_status["execution_routing"] = {
            "inference_provider": cost_summary.get("inference_provider"),
            "inference_model": cost_summary.get("inference_model"),
            "execution_backend": cost_summary.get("execution_backend"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "updated_at": time.time(),
        }
        update_local_task_status(
            tid,
            status,
            history=history,
            last_output=output,
            last_exit_code=exit_code,
            verification_status=verification_status,
        )
        get_execution_audit_service().emit(
            operation_type="execution_result_finalize",
            outcome="success" if status == "completed" else "non_success",
            trace_id=trace.get("trace_id"),
            goal_id=task.get("goal_id"),
            task_id=tid,
            actor_role="hub",
            details={
                "status": status,
                "failure_type": failure_type,
                "execution_backend": cost_summary.get("execution_backend"),
                "inference_provider": cost_summary.get("inference_provider"),
                "inference_model": cost_summary.get("inference_model"),
                "pipeline": dict(pipeline or {}),
            },
        )
        quality_passed = status == "completed" and "[quality_gate] failed:" not in (output or "")
        try:
            self.record_benchmark_sample(
                cost_summary=cost_summary,
                success=(status == "completed"),
                quality_gate_passed=quality_passed,
                proposal_meta=proposal_meta,
            )
        except Exception as exc:
            current_app.logger.warning("Benchmark ingestion failed for task %s: %s", tid, exc)
        return {
            "cost_summary": cost_summary,
            "history_event": history_event,
            "memory_entry": memory_entry,
            "execution_scope": scope_summary,
            "execution_provenance": provenance,
        }

    @staticmethod
    def _derive_execution_scope_summary(history: list[dict]) -> dict:
        allocated = next((item for item in reversed(history) if item.get("event_type") == "execution_scope_allocated"), None)
        released = next((item for item in reversed(history) if item.get("event_type") == "workspace_released"), None)
        if not allocated and not released:
            return {}
        return {
            "workspace_id": (released or allocated or {}).get("workspace_id"),
            "lease_id": (released or allocated or {}).get("lease_id"),
            "lifecycle_status": (released or {}).get("cleanup_state") or "allocated",
            "isolation_mode": "task_scoped_workspace",
            "worker_url": (released or allocated or {}).get("delegated_to"),
            "queue_position": ((allocated or {}).get("execution_scope") or {}).get("queue_position"),
            "executor_container": ((allocated or {}).get("execution_scope") or {}).get("executor_container"),
            "updated_at": time.time(),
        }

    @staticmethod
    def _derive_execution_provenance(history: list[dict]) -> dict:
        fallback = next((item for item in reversed(history) if item.get("event_type") == "hub_worker_fallback"), None)
        mode = "delegated_worker"
        fallback_reason = None
        fallback_details = dict((fallback or {}).get("details") or {})
        if fallback:
            mode = "hub_as_worker_fallback"
            fallback_reason = (
                fallback.get("fallback_reason")
                or fallback_details.get("fallback_reason")
                or "fallback_applied"
            )
        return {
            "execution_mode": mode,
            "fallback_reason": fallback_reason,
            "updated_at": time.time(),
        }


task_execution_tracking_service = TaskExecutionTrackingService()


def get_task_execution_tracking_service() -> TaskExecutionTrackingService:
    return task_execution_tracking_service
