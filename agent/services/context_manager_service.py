from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.db_models import ContextBundleDB, RetrievalRunDB
from agent.repository import context_bundle_repo, retrieval_run_repo
from agent.runtime_policy import normalize_task_kind
from agent.services.context_bundle_service import get_context_bundle_service
from agent.services.rag_service import get_rag_service
from agent.services.repository_registry import get_repository_registry
from agent.services.task_context_policy_service import get_task_context_policy_service


@dataclass(frozen=True)
class _ContextHintData:
    retrieval_intent: str | None = None
    required_context_scope: str | None = None
    preferred_bundle_mode: str | None = None


class ContextManagerService:
    """Shared hub-owned context manager contract for retrieval, budgeting and execution context assembly."""

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
        budget_tokens_by_mode = dict(policy.get("budget_tokens_by_mode") or {})
        window_profile = str(policy.get("window_profile") or "").strip() or None
        neighbor_task_ids = [
            str(value).strip()
            for value in list(policy.get("neighbor_task_ids") or [])
            if str(value).strip()
        ]
        bundle = get_rag_service().retrieve_context_bundle(
            query,
            include_context_text=bool(policy.get("include_context_text", True)),
            max_chunks=policy.get("max_chunks"),
            policy_mode=str(policy.get("mode") or "full"),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
            preferred_bundle_mode=preferred_bundle_mode,
            total_budget_tokens=int(total_budget_tokens) if total_budget_tokens is not None else None,
            budget_tokens_by_mode=budget_tokens_by_mode,
            window_profile=window_profile,
            task_id=parent_task_id,
            goal_id=goal_id,
            neighbor_task_ids=neighbor_task_ids,
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
                    "neighbor_task_ids": neighbor_task_ids,
                    "window_profile": (bundle.get("context_policy") or {}).get("window_profile"),
                    "total_budget_tokens": ((bundle.get("budget") or {}).get("total_tokens")),
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
                    "explainability": bundle.get("explainability") or {},
                    "retrieval_hints": {
                        "task_kind": task_kind,
                        "retrieval_intent": retrieval_intent,
                        "required_context_scope": required_context_scope,
                        "preferred_bundle_mode": preferred_bundle_mode,
                        "neighbor_task_ids": neighbor_task_ids,
                    },
                    "budget": bundle.get("budget") or {},
                    "compaction": bundle.get("compaction") or {},
                    "why_this_context": bundle.get("why_this_context") or {},
                    "selection_trace": bundle.get("selection_trace") or {},
                },
            )
        )

    @staticmethod
    def _default_query(*, task: dict[str, Any], query: str | None = None) -> str:
        candidate = str(query or "").strip()
        if candidate:
            return candidate
        return " ".join(
            item
            for item in [
                str(task.get("title") or "").strip(),
                str(task.get("description") or "").strip(),
                str(task.get("prompt") or "").strip(),
            ]
            if item
        ).strip()

    def ensure_task_context_bundle(
        self,
        *,
        task: dict[str, Any] | None,
        task_id: str | None,
        query: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(task or {})
        effective_task_id = str(task_id or payload.get("id") or "").strip() or None
        existing_bundle_id = str(payload.get("context_bundle_id") or "").strip()
        if existing_bundle_id:
            existing = get_repository_registry().context_bundle_repo.get_by_id(existing_bundle_id)
            if existing is not None:
                return {
                    "created": False,
                    "context_bundle": existing,
                    "context_policy": dict((existing.bundle_metadata or {}).get("context_policy") or {}),
                    "retrieval_hints": dict((existing.bundle_metadata or {}).get("retrieval_hints") or {}),
                    "task_neighborhood": {
                        "neighbor_task_ids": list(
                            (
                                (existing.bundle_metadata or {}).get("retrieval_hints") or {}
                            ).get("neighbor_task_ids")
                            or []
                        ),
                    },
                }

        context_query = self._default_query(task=payload, query=query)
        task_kind = normalize_task_kind(payload.get("task_kind"), context_query)
        context_policy, retrieval_hints, task_neighborhood = get_task_context_policy_service().build_context_policy(
            parent_task=payload,
            data=_ContextHintData(),
            effective_task_kind=task_kind,
        )
        bundle = self.create_context_bundle(
            query=context_query,
            parent_task_id=effective_task_id,
            goal_id=str(payload.get("goal_id") or "").strip() or None,
            context_policy=context_policy,
        )
        return {
            "created": True,
            "context_bundle": bundle,
            "context_policy": dict(context_policy),
            "retrieval_hints": dict(retrieval_hints),
            "task_neighborhood": dict(task_neighborhood),
        }

    def build_cli_execution_context(
        self,
        *,
        prompt: str,
        task_kind: str | None,
        retrieval_intent: str | None = None,
        source_types: list[str] | None = None,
    ) -> tuple[dict[str, object], str]:
        policy = get_context_bundle_service().resolve_context_bundle_policy(None)
        bundle = get_rag_service().retrieve_context_bundle(
            prompt,
            include_context_text=bool(policy.get("include_context_text", True)),
            max_chunks=policy.get("max_chunks"),
            policy_mode=str(policy.get("mode") or "full"),
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            total_budget_tokens=int(policy.get("total_budget_tokens") or 0) or None,
            budget_tokens_by_mode=dict(policy.get("budget_tokens_by_mode") or {}),
            window_profile=str(policy.get("window_profile") or "standard_32k"),
            source_types=source_types,
        )
        grounded_prompt = get_context_bundle_service().build_grounded_prompt(
            prompt=prompt,
            context_text=str(bundle.get("context_text") or ""),
        )
        return bundle, grounded_prompt


context_manager_service = ContextManagerService()


def get_context_manager_service() -> ContextManagerService:
    return context_manager_service
