from __future__ import annotations

import time

from flask import current_app

from agent.llm_benchmarks import estimate_cost_units
from agent.llm_benchmarks import record_benchmark_sample as persist_benchmark_sample
from agent.llm_benchmarks import resolve_benchmark_identity as shared_resolve_benchmark_identity
from agent.runtime_policy import normalize_task_kind
from agent.services.ingestion_service import get_ingestion_service
from agent.services.result_memory_service import get_result_memory_service
from agent.services.task_runtime_service import update_local_task_status
from agent.services.worker_job_service import get_worker_job_service
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens


class TaskExecutionTrackingService:
    """Centralizes proposal persistence and execution tracking for task execution routes."""

    def persist_proposal_result(
        self,
        *,
        tid: str,
        task: dict | None,
        proposal: dict,
        history_event: dict | None = None,
    ) -> dict:
        history = list((task or {}).get("history") or [])
        if history_event:
            event = dict(history_event)
            event.setdefault("timestamp", time.time())
            history.append(event)
            update_local_task_status(tid, "proposing", last_proposal=proposal, history=history)
            return proposal
        update_local_task_status(tid, "proposing", last_proposal=proposal)
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
        return {
            "kind": research_artifact.get("kind") or "research_report",
            "artifact_id": artifact.id,
            "artifact_version_id": version.id,
            "extracted_document_id": document.id if document else None,
            "filename": artifact.latest_filename,
            "media_type": artifact.latest_media_type,
            "task_id": tid,
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
        bench_provider, bench_model = shared_resolve_benchmark_identity(
            proposal_meta,
            current_app.config.get("AGENT_CONFIG", {}) or {},
        )
        bench_task_kind = normalize_task_kind(
            ((proposal_meta or {}).get("routing") or {}).get("task_kind"),
            (task or {}).get("description") or "",
        )
        estimated_tokens = estimate_text_tokens((task or {}).get("description") or "") + estimate_text_tokens(output or "")
        if tool_calls:
            estimated_tokens += estimate_tool_calls_tokens(tool_calls)
        cost_units, pricing_source = estimate_cost_units(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            bench_provider,
            bench_model,
            estimated_tokens,
        )
        return {
            "provider": bench_provider,
            "model": bench_model,
            "task_kind": bench_task_kind,
            "tokens_total": estimated_tokens,
            "cost_units": cost_units,
            "latency_ms": int(execution_duration_ms or 0),
            "pricing_source": pricing_source,
        }

    def record_benchmark_sample(
        self,
        *,
        cost_summary: dict,
        success: bool,
        quality_gate_passed: bool,
    ) -> None:
        persist_benchmark_sample(
            data_dir=current_app.config.get("DATA_DIR") or "data",
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
            provider=str(cost_summary.get("provider") or ""),
            model=str(cost_summary.get("model") or ""),
            task_kind=str(cost_summary.get("task_kind") or ""),
            success=success,
            quality_gate_passed=quality_gate_passed,
            latency_ms=int(cost_summary.get("latency_ms") or 0),
            tokens_total=int(cost_summary.get("tokens_total") or 0),
            cost_units=float(cost_summary.get("cost_units") or 0.0),
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
        memory_entry = self.sync_worker_result_tracking(
            tid=tid,
            task=task,
            status=status,
            output=output,
            trace=trace,
            artifact_refs=artifact_refs,
        )
        update_local_task_status(tid, status, history=history, last_output=output, last_exit_code=exit_code)
        quality_passed = status == "completed" and "[quality_gate] failed:" not in (output or "")
        try:
            self.record_benchmark_sample(
                cost_summary=cost_summary,
                success=(status == "completed"),
                quality_gate_passed=quality_passed,
            )
        except Exception as exc:
            current_app.logger.warning("Benchmark ingestion failed for task %s: %s", tid, exc)
        return {
            "cost_summary": cost_summary,
            "history_event": history_event,
            "memory_entry": memory_entry,
        }


task_execution_tracking_service = TaskExecutionTrackingService()


def get_task_execution_tracking_service() -> TaskExecutionTrackingService:
    return task_execution_tracking_service
