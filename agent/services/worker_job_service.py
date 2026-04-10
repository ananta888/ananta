from __future__ import annotations

import time

from agent.db_models import ContextBundleDB, RetrievalRunDB, WorkerJobDB, WorkerResultDB
from agent.repository import context_bundle_repo, retrieval_run_repo, worker_job_repo, worker_result_repo
from agent.services.worker_capability_service import get_worker_capability_service
from agent.services.rag_service import get_rag_service


class WorkerJobService:
    """Hub-owned worker-job lifecycle and selective context packaging."""

    def __init__(self, rag_service=None) -> None:
        self._rag_service = rag_service or get_rag_service()
        self._worker_capability_service = get_worker_capability_service()

    def create_context_bundle(
        self,
        *,
        query: str,
        parent_task_id: str | None = None,
        goal_id: str | None = None,
        context_policy: dict | None = None,
    ) -> ContextBundleDB:
        policy = dict(context_policy or {})
        task_kind = str(policy.get("task_kind") or "").strip() or None
        retrieval_intent = str(policy.get("retrieval_intent") or "").strip() or None
        required_context_scope = str(policy.get("required_context_scope") or "").strip() or None
        preferred_bundle_mode = str(policy.get("preferred_bundle_mode") or "").strip() or None
        total_budget_tokens = policy.get("total_budget_tokens")
        bundle = self._rag_service.retrieve_context_bundle(
            query,
            include_context_text=bool(policy.get("include_context_text", True)),
            max_chunks=policy.get("max_chunks"),
            policy_mode=str(policy.get("mode") or "full"),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
            preferred_bundle_mode=preferred_bundle_mode,
            total_budget_tokens=int(total_budget_tokens) if total_budget_tokens is not None else None,
        )
        retrieval_run = retrieval_run_repo.save(
            RetrievalRunDB(
                query=query,
                task_id=parent_task_id,
                goal_id=goal_id,
                strategy=bundle.get("strategy") or {},
                chunk_count=len(bundle.get("chunks") or []),
                token_estimate=int(bundle.get("token_estimate") or 0),
                policy_version=str(bundle.get("policy_version") or "v1"),
                run_metadata={
                    "bundle_type": bundle.get("bundle_type") or "retrieval_context",
                    "task_kind": task_kind,
                    "retrieval_intent": retrieval_intent,
                    "required_context_scope": required_context_scope,
                    "preferred_bundle_mode": preferred_bundle_mode,
                },
            )
        )
        return context_bundle_repo.save(
            ContextBundleDB(
                retrieval_run_id=retrieval_run.id,
                task_id=parent_task_id,
                bundle_type="worker_execution_context",
                context_text=str(bundle.get("context_text") or ""),
                chunks=list(bundle.get("chunks") or []),
                token_estimate=int(bundle.get("token_estimate") or 0),
                bundle_metadata={
                    "query": query,
                    "strategy": bundle.get("strategy") or {},
                    "policy_version": bundle.get("policy_version") or "v1",
                    "context_policy": bundle.get("context_policy") or policy,
                    "retrieval_hints": {
                        "task_kind": task_kind,
                        "retrieval_intent": retrieval_intent,
                        "required_context_scope": required_context_scope,
                        "preferred_bundle_mode": preferred_bundle_mode,
                    },
                    "budget": bundle.get("budget") or {},
                    "why_this_context": bundle.get("why_this_context") or {},
                },
            )
        )

    def create_worker_job(
        self,
        *,
        parent_task_id: str,
        subtask_id: str,
        worker_url: str,
        context_bundle_id: str | None,
        allowed_tools: list[str] | None,
        expected_output_schema: dict | None,
        metadata: dict | None = None,
    ) -> WorkerJobDB:
        return worker_job_repo.save(
            WorkerJobDB(
                parent_task_id=parent_task_id,
                subtask_id=subtask_id,
                worker_url=worker_url,
                context_bundle_id=context_bundle_id,
                status="delegated",
                allowed_tools=list(allowed_tools or []),
                expected_output_schema=dict(expected_output_schema or {}),
                job_metadata={
                    **dict(metadata or {}),
                    "tooling_capabilities": self._worker_capability_service.build_tooling_capability_map(),
                },
            )
        )

    def record_worker_result(
        self,
        *,
        worker_job_id: str,
        task_id: str | None,
        worker_url: str,
        status: str,
        output: str | None,
        metadata: dict | None = None,
    ) -> WorkerResultDB:
        job = worker_job_repo.get_by_id(worker_job_id)
        if job is not None:
            job.status = status
            job.updated_at = time.time()
            worker_job_repo.save(job)
        return worker_result_repo.save(
            WorkerResultDB(
                worker_job_id=worker_job_id,
                task_id=task_id,
                worker_url=worker_url,
                status=status,
                output=output,
                result_metadata=dict(metadata or {}),
            )
        )


worker_job_service = WorkerJobService()


def get_worker_job_service() -> WorkerJobService:
    return worker_job_service
