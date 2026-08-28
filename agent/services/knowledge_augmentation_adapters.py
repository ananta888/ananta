"""Semantically identical Agent, MCP, HTTP and n8n policy adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_augmentation_policy_service import KnowledgeAugmentationPolicyService
from ananta_contracts.parametric_knowledge import canonical_sha256

_FORBIDDEN = frozenset(
    {
        "expert_id",
        "manifest_digest",
        "adapter_path",
        "runtime_capability",
        "scope",
        "tenant_id",
        "workspace_id",
        "repository_id",
        "enabled",
    }
)


class KnowledgeAugmentationAdapters:
    """Transport labels affect audit only, never the canonical decision."""

    def __init__(self, policy: KnowledgeAugmentationPolicyService) -> None:
        self._policy = policy

    def for_agent(self, request: Mapping[str, Any], *, hub_context: Mapping[str, Any]) -> dict[str, Any]:
        return self._decide(request, hub_context=hub_context)

    def for_mcp(self, request: Mapping[str, Any], *, hub_context: Mapping[str, Any]) -> dict[str, Any]:
        return self._decide(request, hub_context=hub_context)

    def for_http(self, request: Mapping[str, Any], *, hub_context: Mapping[str, Any]) -> dict[str, Any]:
        return self._decide(request, hub_context=hub_context)

    def for_n8n(self, request: Mapping[str, Any], *, hub_context: Mapping[str, Any]) -> dict[str, Any]:
        return self._decide(request, hub_context=hub_context)

    def _decide(self, request: Mapping[str, Any], *, hub_context: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request).intersection(_FORBIDDEN):
            raise ValueError("knowledge_augmentation_client_authority_denied")
        if set(request).difference({"schema", "profile_id"}):
            raise ValueError("knowledge_augmentation_request_invalid")
        if request.get("schema") != "ananta.knowledge-augmentation-request.v1":
            raise ValueError("knowledge_augmentation_request_invalid")
        return self._policy.decide(
            profile_id=str(request.get("profile_id") or ""),
            global_enabled=bool(hub_context.get("global_enabled")),
            model_enabled=bool(hub_context.get("model_enabled")),
            task_enabled=bool(hub_context.get("task_enabled")),
            domain_enabled=bool(hub_context.get("domain_enabled")),
            data_class_enabled=bool(hub_context.get("data_class_enabled")),
            runtime_ready=bool(hub_context.get("runtime_ready")),
            expert_selected=bool(hub_context.get("expert_selected")),
            citation_required=bool(hub_context.get("citation_required")),
            rag_available=bool(hub_context.get("rag_available", True)),
        ).to_dict()


def default_knowledge_augmentation_adapters() -> KnowledgeAugmentationAdapters:
    """Return the compatibility-safe default used when DMoE is unconfigured."""

    profiles = {"rag-default": {"mode": "rag_only"}}
    return KnowledgeAugmentationAdapters(
        KnowledgeAugmentationPolicyService(profiles=profiles, policy_digest=canonical_sha256(profiles))
    )


__all__ = ["KnowledgeAugmentationAdapters", "default_knowledge_augmentation_adapters"]
