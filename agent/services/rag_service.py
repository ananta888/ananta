from __future__ import annotations

from agent.services.context_bundle_service import get_context_bundle_service
from agent.services.retrieval_service import get_retrieval_service


class RagService:
    """Central RAG facade for routes that need retrieval and grounded prompts."""

    def __init__(self, retrieval_service=None, context_bundle_service=None) -> None:
        self._retrieval_service = retrieval_service or get_retrieval_service()
        self._context_bundle_service = context_bundle_service or get_context_bundle_service()

    def retrieve_context_bundle(
        self,
        query: str,
        *,
        include_context_text: bool = True,
        max_chunks: int | None = None,
        policy_mode: str = "full",
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        required_context_scope: str | None = None,
        preferred_bundle_mode: str | None = None,
        total_budget_tokens: int | None = None,
        budget_tokens_by_mode: dict[str, int] | None = None,
        window_profile: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, object]:
        context_payload = self._retrieval_service.retrieve_context(
            query,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            task_id=task_id,
            goal_id=goal_id,
            neighbor_task_ids=neighbor_task_ids,
            source_types=source_types,
        )
        return self._context_bundle_service.build_bundle(
            query=query,
            context_payload=context_payload,
            include_context_text=include_context_text,
            max_chunks=max_chunks,
            policy_mode=policy_mode,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
            preferred_bundle_mode=preferred_bundle_mode,
            total_budget_tokens=total_budget_tokens,
            budget_tokens_by_mode=budget_tokens_by_mode,
            window_profile=window_profile,
        )

    def build_execution_context(
        self,
        prompt: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        source_types: list[str] | None = None,
    ) -> tuple[dict[str, object], str]:
        bundle = self.retrieve_context_bundle(
            prompt,
            include_context_text=True,
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            source_types=source_types,
        )
        grounded_prompt = self._context_bundle_service.build_grounded_prompt(
            prompt=prompt,
            context_text=str(bundle.get("context_text") or ""),
        )
        return bundle, grounded_prompt


rag_service = RagService()


def get_rag_service() -> RagService:
    return rag_service
