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
    ) -> dict[str, object]:
        context_payload = self._retrieval_service.retrieve_context(query)
        return self._context_bundle_service.build_bundle(
            query=query,
            context_payload=context_payload,
            include_context_text=include_context_text,
            max_chunks=max_chunks,
            policy_mode=policy_mode,
        )

    def build_execution_context(self, prompt: str) -> tuple[dict[str, object], str]:
        bundle = self.retrieve_context_bundle(prompt, include_context_text=True)
        grounded_prompt = self._context_bundle_service.build_grounded_prompt(
            prompt=prompt,
            context_text=str(bundle.get("context_text") or ""),
        )
        return bundle, grounded_prompt


rag_service = RagService()


def get_rag_service() -> RagService:
    return rag_service
