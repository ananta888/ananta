from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
)
from agent.services.evolution import EvolutionTrigger, EvolutionTriggerType


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPResourceSpec:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


class MCPRegistryService:
    """Central registry/dispatch for MCP tools and resources."""

    _TOOLS: tuple[MCPToolSpec, ...] = (
        MCPToolSpec(
            name="health.get",
            description="Read hub health status via existing health builder.",
            input_schema={
                "type": "object",
                "properties": {"basic": {"type": "boolean"}},
                "additionalProperties": False,
            },
        ),
        MCPToolSpec(
            name="providers.list_models",
            description="List OpenAI-compatible model catalog known by the hub.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        MCPToolSpec(
            name="tasks.list",
            description="List tasks with optional status filter and pagination.",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        ),
        MCPToolSpec(
            name="tasks.get",
            description="Read a single task by id.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        MCPToolSpec(
            name="artifacts.list",
            description="List uploaded artifacts.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        MCPToolSpec(
            name="knowledge.list_collections",
            description="List knowledge collections.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        MCPToolSpec(
            name="codecompass.architecture_overview",
            description="Budgeted hierarchical architecture overview for a project question.",
            input_schema={
                "type": "object",
                "required": ["query"],
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "profile": {"type": "string"},
                    "revision": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="codecompass.architecture_expand",
            description="Expand one architecture handle from a previous overview.",
            input_schema={
                "type": "object",
                "required": ["handle"],
                "additionalProperties": False,
                "properties": {
                    "handle": {"type": "string"},
                    "query": {"type": "string"},
                    "revision": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="codecompass.retrieve",
            description=(
                "Retrieve budgeted CodeCompass evidence for a project question. "
                "Uses the same hybrid retrieval contract as the Ananta worker. "
                "Do not pass Qdrant collections or credentials."
            ),
            input_schema={
                "type": "object",
                "required": ["query"],
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "hybrid", "vector", "exact", "graph"],
                    },
                    "requested_signals": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["exact", "graph", "vector"]},
                    },
                    "task_kind": {"type": "string"},
                    "revision": {"type": "string"},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_chars": {"type": "integer", "minimum": 256, "maximum": 32000},
                    "continuation_handle": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="evolution.providers.list",
            description="List Evolution providers, health and policy-visible configuration.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        MCPToolSpec(
            name="evolution.analyze",
            description="Run a policy-controlled Evolution analysis for a hub-owned task.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "provider_name": {"type": "string"},
                    "objective": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        # CTA-013: classroom transcript assistant triggers. External
        # transcript/room systems (or n8n) push segments here; the
        # classroom gateway handles dedup via TriggerEngine.
        MCPToolSpec(
            name="classroom.transcript_event",
            description="Push a classroom transcript segment; triggers analysis up to a TeacherActionCard.",
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "zoom_room_id": {"type": "string"},
                    "room_label": {"type": "string"},
                    "module_id_hint": {"type": "string"},
                    "task_id_hint": {"type": "string"},
                    "timestamp": {"type": ["string", "number"]},
                    "sequence_no": {"type": "integer", "minimum": 0},
                    "speaker_role": {"type": "string", "enum": ["student", "teacher", "unknown"]},
                    "speaker_label": {"type": "string"},
                    "text_segment": {"type": "string"},
                    "trigger_mode": {"type": "string"},
                },
                "required": ["event_id", "session_id", "text_segment"],
                "additionalProperties": False,
            },
        ),
        MCPToolSpec(
            name="classroom.reanalyze",
            description="Re-run classroom analysis for an existing TeacherActionCard.",
            input_schema={
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
                "additionalProperties": False,
            },
        ),
        MCPToolSpec(
            name="evolution.proposals.list",
            description="Read Evolution runs and proposals for a task.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
    )

    _RESOURCES: tuple[MCPResourceSpec, ...] = (
        MCPResourceSpec(uri="ananta://system/health", name="System Health", description="Current hub health snapshot."),
        MCPResourceSpec(
            uri="ananta://providers/models",
            name="Providers Models",
            description="OpenAI-compatible provider model list.",
        ),
        MCPResourceSpec(
            uri="ananta://tasks/recent", name="Recent Tasks", description="Recent tasks from hub task queue."
        ),
        MCPResourceSpec(uri="ananta://artifacts/list", name="Artifacts", description="All known artifacts."),
        MCPResourceSpec(
            uri="ananta://knowledge/collections", name="Knowledge Collections", description="All knowledge collections."
        ),
        MCPResourceSpec(
            uri="ananta://evolution/providers",
            name="Evolution Providers",
            description="Evolution provider discovery and health.",
        ),
    )

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "description": item.description,
                "inputSchema": item.input_schema,
            }
            for item in self._TOOLS
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": item.uri,
                "name": item.name,
                "description": item.description,
                "mimeType": item.mime_type,
            }
            for item in self._RESOURCES
        ]

    def call_tool(self, *, name: str, arguments: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        if name == "health.get":
            basic_mode = bool(args.get("basic", True))
            health_builder = context["health_builder"]
            return {"content": [{"type": "json", "json": health_builder(basic_mode=basic_mode)}]}

        if name == "providers.list_models":
            openai_compat_service = context["openai_compat_service"]
            return {"content": [{"type": "json", "json": {"items": openai_compat_service.list_models()}}]}

        if name == "tasks.list":
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

        if name == "tasks.get":
            task_id = str(args.get("task_id") or "").strip()
            if not task_id:
                raise ValueError("task_id_required")
            task_repo = context["task_repo"]
            task = task_repo.get_by_id(task_id)
            if task is None:
                raise KeyError("task_not_found")
            return {"content": [{"type": "json", "json": task.model_dump()}]}

        if name == "artifacts.list":
            artifact_repo = context["artifact_repo"]
            items = [
                item.model_dump()
                for item in artifact_repo.get_all()
                if is_artifact_visible_on_generic_surfaces(item)
            ]
            return {"content": [{"type": "json", "json": {"items": items, "count": len(items)}}]}

        if name == "knowledge.list_collections":
            collection_repo = context["knowledge_collection_repo"]
            items = [item.model_dump() for item in collection_repo.get_all()]
            return {"content": [{"type": "json", "json": {"items": items, "count": len(items)}}]}

        if name in {"codecompass.architecture_overview", "codecompass.architecture_expand"}:
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

        if name == "codecompass.retrieve":
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

        if name == "evolution.providers.list":
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

        if name == "evolution.analyze":
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

        if name == "classroom.transcript_event":
            classroom_cfg = (context.get("agent_config") or {}).get("classroom") or {}
            if not bool(classroom_cfg.get("enabled", False)):
                raise ValueError("classroom_disabled")
            gateway = context["classroom_gateway"]
            result = gateway.process_event(args, source_adapter="mcp")
            if result.get("status") == "error":
                raise ValueError(str(result.get("reason_code") or "classroom_event_invalid"))
            return {"content": [{"type": "json", "json": result}]}

        if name == "classroom.reanalyze":
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

        if name == "evolution.proposals.list":
            task_id = str(args.get("task_id") or "").strip()
            if not task_id:
                raise ValueError("task_id_required")
            limit = max(1, min(int(args.get("limit", 50)), 200))
            payload = context["evolution_service"].task_read_model(task_id, limit=limit)
            return {"content": [{"type": "json", "json": payload}]}

        raise KeyError("unknown_tool")

    def read_resource(self, *, uri: str, context: dict[str, Any]) -> dict[str, Any]:
        normalized_uri = str(uri or "").strip()
        if normalized_uri == "ananta://system/health":
            payload = context["health_builder"](basic_mode=True)
            return {"contents": [{"uri": normalized_uri, "mimeType": "application/json", "text": payload}]}
        if normalized_uri == "ananta://providers/models":
            payload = {"items": context["openai_compat_service"].list_models()}
            return {"contents": [{"uri": normalized_uri, "mimeType": "application/json", "text": payload}]}
        if normalized_uri == "ananta://tasks/recent":
            tasks = context["task_query_service"].list_tasks(
                status_filter="",
                agent_filter=None,
                since_filter=None,
                until_filter=None,
                limit=20,
                offset=0,
            )
            return {
                "contents": [
                    {
                        "uri": normalized_uri,
                        "mimeType": "application/json",
                        "text": {"items": tasks, "count": len(tasks)},
                    }
                ]
            }
        if normalized_uri == "ananta://artifacts/list":
            items = [
                item.model_dump()
                for item in context["artifact_repo"].get_all()
                if is_artifact_visible_on_generic_surfaces(item)
            ]
            return {
                "contents": [
                    {
                        "uri": normalized_uri,
                        "mimeType": "application/json",
                        "text": {"items": items, "count": len(items)},
                    }
                ]
            }
        if normalized_uri == "ananta://knowledge/collections":
            items = [item.model_dump() for item in context["knowledge_collection_repo"].get_all()]
            return {
                "contents": [
                    {
                        "uri": normalized_uri,
                        "mimeType": "application/json",
                        "text": {"items": items, "count": len(items)},
                    }
                ]
            }
        if normalized_uri == "ananta://evolution/providers":
            payload = {
                "providers": context["evolution_service"].list_providers(),
                "health": context["evolution_service"].provider_health(),
                "config": context.get("evolution_config") or {},
            }
            return {"contents": [{"uri": normalized_uri, "mimeType": "application/json", "text": payload}]}
        raise KeyError("resource_not_found")


mcp_registry_service = MCPRegistryService()


def get_mcp_registry_service() -> MCPRegistryService:
    return mcp_registry_service
