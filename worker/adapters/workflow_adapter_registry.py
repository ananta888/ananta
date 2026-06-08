"""Workflow Adapter Registry — LangChain, LangGraph, n8n, webhook, mock (LCG-005)."""
from __future__ import annotations

import logging
from typing import Any

from worker.adapters.workflow_adapter_base import (
    WorkflowAdapter, WorkflowAdapterDescriptor,
)

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, WorkflowAdapter] = {}


def register_adapter(adapter: WorkflowAdapter) -> None:
    desc = adapter.descriptor()
    _REGISTRY[desc.kind] = adapter
    logger.debug("workflow adapter registered: %s (%s)", desc.adapter_id, desc.kind)


def get_adapter(kind: str) -> WorkflowAdapter | None:
    return _REGISTRY.get(kind)


def list_adapters() -> list[WorkflowAdapterDescriptor]:
    return [a.descriptor() for a in _REGISTRY.values()]


def list_adapters_as_dicts() -> list[dict[str, Any]]:
    return [d.as_dict() for d in list_adapters()]


def _load_defaults() -> None:
    """Lazy-load adapters when the registry is first accessed.

    Not-installed optional dependencies produce degraded/blocked status,
    not import crashes.
    """
    if _REGISTRY:
        return

    # LangChain (optional)
    try:
        from worker.adapters.langchain_adapter import LangChainAdapter
        register_adapter(LangChainAdapter())
    except ImportError as exc:
        logger.debug("LangChainAdapter not loaded: %s", exc)

    # LangGraph (optional)
    try:
        from worker.adapters.langgraph_adapter import LangGraphAdapter
        register_adapter(LangGraphAdapter())
    except ImportError as exc:
        logger.debug("LangGraphAdapter not loaded: %s", exc)


def get_registry() -> dict[str, WorkflowAdapter]:
    _load_defaults()
    return dict(_REGISTRY)
