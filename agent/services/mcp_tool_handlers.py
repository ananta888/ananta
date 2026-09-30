"""Tool handlers of the Hub MCP registry.

Split out of ``mcp_registry_service.MCPRegistryService.call_tool`` (SRP/OCP):
each MCP tool has one handler ``(name, args, context) -> MCP content`` and
``MCP_TOOL_HANDLERS`` maps tool names to handlers, so adding a tool adds a
handler and a table entry instead of another branch in ``call_tool``.
"""

from __future__ import annotations

from typing import Any, Callable

from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
)
from agent.services.evolution import EvolutionTrigger, EvolutionTriggerType


def _default_layer_profile(profiles: Any) -> str:
    """``default`` when present, else the only or the first profile; ``""`` without profiles."""
    names = [
        str(p.get("profile_id") or p.get("id") or "") if isinstance(p, dict) else str(p) for p in list(profiles or [])
    ]
    names = [name for name in names if name]
    if "default" in names:
        return "default"
    return names[0] if names else ""


def call_knowledge_tool(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if name == "knowledge.list_collections":
        collection_repo = context["knowledge_collection_repo"]
        items = [item.model_dump() for item in collection_repo.get_all()]
        result: Any = {"items": items, "count": len(items)}
    else:
        adapter = context["knowledge_augmentation_adapters"]
        result = adapter.for_mcp(
            {
                "schema": "ananta.knowledge-augmentation-request.v1",
                "profile_id": str(args.get("profile_id") or ""),
            },
            hub_context=dict(context.get("knowledge_augmentation_context") or {}),
        )
    return {"content": [{"type": "json", "json": result}]}


def _tool_health_get(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    basic_mode = bool(args.get("basic", True))
    health_builder = context["health_builder"]
    return {"content": [{"type": "json", "json": health_builder(basic_mode=basic_mode)}]}


def _tool_providers_list_models(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    openai_compat_service = context["openai_compat_service"]
    return {"content": [{"type": "json", "json": {"items": openai_compat_service.list_models()}}]}


def _tool_tasks_list(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query_service = context["task_query_service"]
    limit = max(1, min(int(args.get("limit", 20)), 200))
    offset = max(0, int(args.get("offset", 0)))
    status = str(args.get("status") or "").strip().lower()
    tasks = query_service.list_tasks(
        status_filter=status,
        agent_filter=None,
        since_filter=None,
        until_filter=None,
        limit=limit,
        offset=offset,
    )
    return {"content": [{"type": "json", "json": {"items": tasks, "count": len(tasks)}}]}


def _tool_tasks_get(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id_required")
    task_repo = context["task_repo"]
    task = task_repo.get_by_id(task_id)
    if task is None:
        raise KeyError("task_not_found")
    return {"content": [{"type": "json", "json": task.model_dump()}]}


def _tool_artifacts_list(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    artifact_repo = context["artifact_repo"]
    items = [item.model_dump() for item in artifact_repo.get_all() if is_artifact_visible_on_generic_surfaces(item)]
    return {"content": [{"type": "json", "json": {"items": items, "count": len(items)}}]}


def _tool_knowledge(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return call_knowledge_tool(name=name, args=args, context=context)


def _tool_codecompass_architecture_navigation(
    *, name: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    from agent.services.tools.codecompass_architecture_tools import (
        codecompass_architecture_expand,
        codecompass_architecture_overview,
    )

    args["capability"] = context.get("codecompass_capability")
    handler = (
        codecompass_architecture_overview
        if name == "codecompass.architecture_overview"
        else codecompass_architecture_expand
    )
    result = handler(workspace_dir="", arguments=args, tool_call_id=f"mcp:{name}")
    return {"content": [{"type": "json", "json": result}]}


def _tool_codecompass_layers_heads(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    service = get_codecompass_layer_service()
    profiles = service.list_profiles()
    # without profile_id: the default profile's head (was always null)
    profile_id = str(args.get("profile_id") or "") or _default_layer_profile(profiles)
    payload = {
        "profiles": profiles,
        "profile_id": profile_id or None,
        "head": service.show_head(profile_id) if profile_id else None,
    }
    return {"content": [{"type": "json", "json": payload}]}


def _tool_codecompass_layers_plan(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    plan = get_codecompass_layer_service().plan_update(
        old_manifest=args.get("old_manifest") or {},
        new_manifest=args.get("new_manifest") or {},
        profile={},
        profile_id=str(args.get("profile_id") or "default"),
    )
    return {"content": [{"type": "json", "json": plan}]}


def _tool_codecompass_architecture_intelligence(
    *, name: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    from agent.services.codecompass_architecture_intelligence_service import (
        get_codecompass_architecture_intelligence_service,
    )
    from agent.services.tools.codecompass_architecture_tools import _load_architecture_graph

    nodes, edges = _load_architecture_graph({})
    result = get_codecompass_architecture_intelligence_service().analyze(
        {"nodes": nodes, "edges": edges},
        snapshot_ref=str(args.get("snapshot_ref") or ""),
        revision=str(args.get("revision") or ""),
    )
    return {"content": [{"type": "json", "json": result}]}


def _tool_codecompass_analytics_query(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from agent.services.codecompass_duckdb_analytics_service import (
        get_codecompass_duckdb_analytics_service,
    )
    from worker.retrieval.vector_store_contract import VectorStoreError

    capability = context.get("codecompass_capability")
    try:
        result = get_codecompass_duckdb_analytics_service().query(
            str(args.get("template") or ""),
            capability=capability if isinstance(capability, dict) else None,
            params={"kind": args.get("kind")} if args.get("kind") else None,
        )
    except VectorStoreError as error:
        if str(getattr(error, "reason", "") or error) not in {"duckdb_snapshot_missing", "empty_scope"}:
            raise
        # no DuckDB analytics snapshot (the stack indexes into another vector backend) or no scope:
        # a clear, bounded "unavailable" instead of an exception
        result = {
            "status": "unavailable",
            "template": str(args.get("template") or ""),
            "reason": str(getattr(error, "reason", "") or error),
            "hint": "DuckDB analytics need a CodeCompass DuckDB snapshot "
            "(.rag/codecompass/duckdb); use codecompass.search / retrieve instead.",
        }
    return {"content": [{"type": "json", "json": result}]}


def _tool_codecompass_rlm_analyze(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from agent.services.codecompass_rlm_service import get_codecompass_rlm_service

    result = get_codecompass_rlm_service().analyze(
        str(args.get("query") or ""),
        capability=(
            context.get("codecompass_capability") if isinstance(context.get("codecompass_capability"), dict) else None
        ),
        enabled=bool(args.get("enabled", False)),
        max_depth=int(args.get("max_depth") or 3),
        max_fanout=int(args.get("max_fanout") or 4),
    )
    return {"content": [{"type": "json", "json": result}]}


def _tool_codecompass_retrieve(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from agent.services.codecompass_agentic_retrieval_service import (
        get_codecompass_agentic_retrieval_service,
    )

    capability = context.get("codecompass_capability")
    if isinstance(capability, dict) and not capability:
        capability = None
    result = get_codecompass_agentic_retrieval_service().retrieve_from_tool_args(
        args,
        capability=capability if isinstance(capability, dict) else None,
    )
    return {"content": [{"type": "json", "json": result}]}


def _tool_evolution_providers_list(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    evolution_service = context["evolution_service"]
    return {
        "content": [
            {
                "type": "json",
                "json": {
                    "providers": evolution_service.list_providers(),
                    "health": evolution_service.provider_health(),
                    "config": context.get("evolution_config") or {},
                },
            }
        ]
    }


def _tool_evolution_analyze(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id_required")
    trigger = EvolutionTrigger(
        trigger_type=EvolutionTriggerType.MANUAL,
        source="mcp",
        reason=str(args.get("reason") or "mcp_evolution_analyze").strip(),
    )
    result = context["evolution_service"].analyze_task(
        task_id,
        objective=str(args.get("objective") or "").strip() or None,
        provider_name=str(args.get("provider_name") or "").strip() or None,
        config=context.get("agent_config") or {},
        trigger=trigger,
        persist=True,
    )
    return {
        "content": [
            {
                "type": "json",
                "json": {
                    "run_id": result.run_id,
                    "provider_name": result.provider_name,
                    "status": result.status,
                    "proposal_ids": list(result.proposal_ids),
                    "summary": result.result.summary,
                },
            }
        ]
    }


def _tool_classroom_transcript_event(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    classroom_cfg = (context.get("agent_config") or {}).get("classroom") or {}
    if not bool(classroom_cfg.get("enabled", False)):
        raise ValueError("classroom_disabled")
    gateway = context["classroom_gateway"]
    result = gateway.process_event(args, source_adapter="mcp")
    if result.get("status") == "error":
        raise ValueError(str(result.get("reason_code") or "classroom_event_invalid"))
    return {"content": [{"type": "json", "json": result}]}


def _tool_classroom_reanalyze(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    classroom_cfg = (context.get("agent_config") or {}).get("classroom") or {}
    if not bool(classroom_cfg.get("enabled", False)):
        raise ValueError("classroom_disabled")
    card_id = str(args.get("card_id") or "").strip()
    if not card_id:
        raise ValueError("card_id_required")
    card_service = context["classroom_card_service"]
    card = card_service.get_card(card_id)
    if card is None:
        raise KeyError("card_not_found")
    gateway = context["classroom_gateway"]
    replay = {
        "event_id": f"{card['source_event_id']}-reanalyze-{card_id}",
        "session_id": str(card.get("source_event_id") or card_id),
        "zoom_room_id": card.get("zoom_room"),
        "speaker_label_hash": card.get("student_alias"),
        "text_segment": card.get("question_summary"),
        "trigger_mode": "reanalyze",
    }
    result = gateway.process_event(replay, source_adapter="mcp")
    return {"content": [{"type": "json", "json": result}]}


def _tool_evolution_proposals_list(*, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id_required")
    limit = max(1, min(int(args.get("limit", 50)), 200))
    payload = context["evolution_service"].task_read_model(task_id, limit=limit)
    return {"content": [{"type": "json", "json": payload}]}


MCPToolHandler = Callable[..., dict[str, Any]]

MCP_TOOL_HANDLERS: dict[str, MCPToolHandler] = {
    "health.get": _tool_health_get,
    "providers.list_models": _tool_providers_list_models,
    "tasks.list": _tool_tasks_list,
    "tasks.get": _tool_tasks_get,
    "artifacts.list": _tool_artifacts_list,
    "knowledge.list_collections": _tool_knowledge,
    "knowledge_augmentation.decide": _tool_knowledge,
    "codecompass.architecture_expand": _tool_codecompass_architecture_navigation,
    "codecompass.architecture_overview": _tool_codecompass_architecture_navigation,
    "codecompass.layers_heads": _tool_codecompass_layers_heads,
    "codecompass.layers_plan": _tool_codecompass_layers_plan,
    "codecompass.architecture_intelligence": _tool_codecompass_architecture_intelligence,
    "codecompass.analytics_query": _tool_codecompass_analytics_query,
    "codecompass.rlm_analyze": _tool_codecompass_rlm_analyze,
    "codecompass.retrieve": _tool_codecompass_retrieve,
    "evolution.providers.list": _tool_evolution_providers_list,
    "evolution.analyze": _tool_evolution_analyze,
    "classroom.transcript_event": _tool_classroom_transcript_event,
    "classroom.reanalyze": _tool_classroom_reanalyze,
    "evolution.proposals.list": _tool_evolution_proposals_list,
}


__all__ = ["MCP_TOOL_HANDLERS", "MCPToolHandler", "call_knowledge_tool"]
