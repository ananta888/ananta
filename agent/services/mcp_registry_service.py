from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
)
from agent.services.evolution import EvolutionTrigger, EvolutionTriggerType  # noqa: F401 - compatibility re-export
from agent.services.mcp_tool_handlers import (  # noqa: F401 - _default_layer_profile: compatibility re-export
    MCP_TOOL_HANDLERS,
    _default_layer_profile,
    call_knowledge_tool,
)


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    access_class: str = "read"
    risk_class: str = "low"
    lifecycle: str = "enabled"
    default_enabled: bool = False


@dataclass(frozen=True)
class MCPResourceSpec:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    risk_class: str = "low"
    lifecycle: str = "enabled"


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
            name="knowledge_augmentation.decide",
            description="Resolve the Hub-owned parametric expert/RAG policy without selecting an expert.",
            input_schema={
                "type": "object",
                "required": ["profile_id"],
                "properties": {"profile_id": {"type": "string"}},
                "additionalProperties": False,
            },
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
            name="codecompass.layers_heads",
            description="List incremental CodeCompass layer heads/profiles. Read-only.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"profile_id": {"type": "string"}},
            },
        ),
        MCPToolSpec(
            name="codecompass.layers_plan",
            description="Dry-run an incremental index update plan from two snapshot manifests.",
            input_schema={
                "type": "object",
                "required": ["old_manifest", "new_manifest"],
                "additionalProperties": False,
                "properties": {
                    "old_manifest": {"type": "object"},
                    "new_manifest": {"type": "object"},
                    "profile_id": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="codecompass.architecture_intelligence",
            description=(
                "Read-only architecture intelligence: communities, smells, health. "
                "Derived projection, not source evidence."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "snapshot_ref": {"type": "string"},
                    "revision": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="codecompass.analytics_query",
            description="Run a named CodeCompass DuckDB analytics template. No free SQL.",
            input_schema={
                "type": "object",
                "required": ["template"],
                "additionalProperties": False,
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": [
                            "document_counts_by_kind",
                            "paths_for_kind",
                            "graph_relation_counts",
                            "snapshot_identity",
                        ],
                    },
                    "kind": {"type": "string"},
                },
            },
        ),
        MCPToolSpec(
            name="codecompass.rlm_analyze",
            description="Optional recursive CodeCompass analysis. Disabled queries fall back to hybrid retrieval.",
            input_schema={
                "type": "object",
                "required": ["query"],
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "max_depth": {"type": "integer"},
                    "max_fanout": {"type": "integer"},
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
            access_class="write",
            risk_class="high",
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
            access_class="write",
            risk_class="high",
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
            access_class="write",
            risk_class="high",
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

    def tool_specs(self) -> tuple[MCPToolSpec, ...]:
        return tuple(self._TOOLS)

    def resource_specs(self) -> tuple[MCPResourceSpec, ...]:
        return tuple(self._RESOURCES)

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
        if name.startswith("codecompass."):
            from agent.services.codecompass_authority_policy import assert_no_client_authority

            assert_no_client_authority(args)
        handler = MCP_TOOL_HANDLERS.get(name)
        if handler is None:
            raise KeyError("unknown_tool")
        return handler(name=name, args=args, context=context)

    _call_knowledge_tool = staticmethod(call_knowledge_tool)

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
