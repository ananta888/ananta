"""Read-only Hub catalog for worker-owned workflow adapter capabilities.

The catalog contains no worker instances, provider configuration or
credentials.  ``ready`` means that the Hub queue bridge exists; the selected
worker still validates its own runtime configuration before execution.
"""
from __future__ import annotations

from copy import deepcopy

_SAFE_ADAPTER_DESCRIPTORS: dict[str, dict[str, object]] = {
    "langchain": {
        "adapter_id": "adapter.langchain",
        "display_name": "LangChain",
        "kind": "langchain",
        "status": "unavailable",
        "enabled": False,
        "reason": "workflow_runtime_bridge_unavailable",
        "capabilities": ["dry_run", "runnable", "structured_output", "codecompass_retriever"],
        "version": "1.0",
    },
    "langgraph": {
        "adapter_id": "adapter.langgraph",
        "display_name": "LangGraph",
        "kind": "langgraph",
        "status": "ready",
        "enabled": True,
        "reason": "hub_task_queue_bridge_ready",
        "capabilities": ["dry_run", "stateful_task", "human_in_loop", "checkpointing"],
        "version": "1.1",
        "execution_mode": "hub_delegated",
    },
}


class WorkflowAdapterCatalogService:
    def list_descriptors(self) -> list[dict[str, object]]:
        return [deepcopy(_SAFE_ADAPTER_DESCRIPTORS[key]) for key in sorted(_SAFE_ADAPTER_DESCRIPTORS)]

    def get_descriptor(self, kind: str) -> dict[str, object] | None:
        descriptor = _SAFE_ADAPTER_DESCRIPTORS.get(str(kind or "").strip().lower())
        return deepcopy(descriptor) if descriptor is not None else None


workflow_adapter_catalog_service = WorkflowAdapterCatalogService()
