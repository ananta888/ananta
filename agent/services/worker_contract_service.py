from __future__ import annotations

from agent.models import WorkerExecutionContextContract, WorkerRoutingDecisionContract
from agent.services.worker_routing_policy_utils import derive_research_specialization
from agent.services.task_execution_policy_service import normalize_allowed_tools


class WorkerContractService:
    """Builds normalized worker routing and execution contracts for hub-owned flows."""

    def build_routing_decision(
        self,
        *,
        agent_url: str | None,
        selected_by_policy: bool,
        task_kind: str | None,
        required_capabilities: list[str] | None,
        selection=None,
        preferred_backend: str | None = None,
    ) -> dict:
        normalized_required = [str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()]
        reasons = list(getattr(selection, "reasons", None) or (["manual_override"] if agent_url else ["no_worker_available"]))
        return WorkerRoutingDecisionContract(
            worker_url=agent_url,
            selected_by_policy=selected_by_policy,
            strategy=str(getattr(selection, "strategy", None) or ("capability_quality_load_match" if selected_by_policy else "manual_override")),
            reasons=reasons,
            matched_capabilities=list(getattr(selection, "matched_capabilities", None) or []),
            matched_roles=list(getattr(selection, "matched_roles", None) or []),
            task_kind=str(task_kind or "").strip() or None,
            required_capabilities=normalized_required,
            research_specialization=derive_research_specialization(None, task_kind, normalized_required),
            preferred_backend=str(preferred_backend or "").strip() or None,
        ).model_dump()

    def build_execution_context(
        self,
        *,
        instructions: str,
        context_bundle,
        context_policy: dict | None,
        allowed_tools: list[str] | None,
        expected_output_schema: dict | None,
        routing_decision: dict | None,
    ) -> dict:
        return WorkerExecutionContextContract(
            instructions=instructions,
            context_bundle_id=getattr(context_bundle, "id", None),
            context={
                "context_text": getattr(context_bundle, "context_text", None),
                "chunks": list(getattr(context_bundle, "chunks", None) or []),
                "token_estimate": int(getattr(context_bundle, "token_estimate", 0) or 0),
                "bundle_metadata": dict(getattr(context_bundle, "bundle_metadata", None) or {}),
            },
            context_policy=dict(
                context_policy
                or dict(getattr(context_bundle, "bundle_metadata", None) or {}).get("context_policy")
                or {}
            ),
            allowed_tools=normalize_allowed_tools(allowed_tools),
            expected_output_schema=dict(expected_output_schema or {}),
            routing=dict(routing_decision or {}) or None,
        ).model_dump()

    def build_job_metadata(
        self,
        *,
        routing_decision: dict | None,
        task_kind: str | None,
        required_capabilities: list[str] | None,
        context_policy: dict | None = None,
        extra_metadata: dict | None = None,
    ) -> dict:
        return {
            **dict(extra_metadata or {}),
            "routing_decision": dict(routing_decision or {}),
            "task_kind": str(task_kind or "").strip() or None,
            "required_capabilities": [str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()],
            "context_policy": dict(context_policy or {}),
        }


worker_contract_service = WorkerContractService()


def get_worker_contract_service() -> WorkerContractService:
    return worker_contract_service
