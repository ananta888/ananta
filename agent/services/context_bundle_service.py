from __future__ import annotations


class ContextBundleService:
    """Builds worker-facing context bundles from retrieval output."""

    def build_bundle(
        self,
        *,
        query: str,
        context_payload: dict[str, object],
        include_context_text: bool = True,
    ) -> dict[str, object]:
        payload = dict(context_payload or {})
        if not include_context_text:
            payload.pop("context_text", None)
        payload.setdefault("query", query)
        payload.setdefault("policy_version", "v1")
        payload.setdefault("chunks", [])
        payload.setdefault("strategy", {})
        payload.setdefault("token_estimate", 0)
        payload["bundle_type"] = "retrieval_context"
        return payload

    def build_grounded_prompt(self, *, prompt: str, context_text: str) -> str:
        return (
            "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
            f"Frage:\n{prompt}\n\n"
            f"Kontext:\n{context_text}"
        )


context_bundle_service = ContextBundleService()


def get_context_bundle_service() -> ContextBundleService:
    return context_bundle_service
