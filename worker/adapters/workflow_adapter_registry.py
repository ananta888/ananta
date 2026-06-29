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
    _load_defaults()
    return [a.descriptor() for a in _REGISTRY.values()]


def list_adapters_as_dicts() -> list[dict[str, Any]]:
    return [d.as_dict() for d in list_adapters()]


def _load_defaults() -> None:
    if _REGISTRY:
        return

    lc_config = None
    lg_config = None

    try:
        import flask
        if flask.has_app_context():
            from flask import current_app
            agent_cfg = current_app.config.get("AGENT_CONFIG") or {}
            providers = agent_cfg.get("providers") or {}
            lc_raw = providers.get("langchain")
            lg_raw = providers.get("langgraph")
            if isinstance(lc_raw, dict):
                try:
                    from agent.providers.lc_lg import LangChainProviderConfig
                    lc_config = LangChainProviderConfig(**lc_raw)
                except Exception as exc:
                    logger.warning("langchain provider config invalid, using default_off: %s", exc)
            if isinstance(lg_raw, dict):
                try:
                    from agent.providers.lc_lg import LangGraphProviderConfig
                    lg_config = LangGraphProviderConfig(**lg_raw)
                except Exception as exc:
                    logger.warning("langgraph provider config invalid, using default_off: %s", exc)
    except Exception:
        pass

    try:
        from worker.adapters.langchain_adapter import LangChainAdapter
        register_adapter(LangChainAdapter(lc_config))
    except ImportError as exc:
        logger.debug("LangChainAdapter not loaded: %s", exc)

    try:
        from worker.adapters.langgraph_adapter import LangGraphAdapter
        register_adapter(LangGraphAdapter(lg_config))
    except ImportError as exc:
        logger.debug("LangGraphAdapter not loaded: %s", exc)


def get_registry() -> dict[str, WorkflowAdapter]:
    _load_defaults()
    return dict(_REGISTRY)
