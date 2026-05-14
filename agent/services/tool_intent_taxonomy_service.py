from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolIntentDefinition:
    intent: str
    tool_names: tuple[str, ...]
    required_args: tuple[str, ...]
    optional_args: tuple[str, ...]
    risk_class: str
    allowed_remap_targets: tuple[str, ...]
    tool_class: str


_DEFINITIONS: tuple[ToolIntentDefinition, ...] = (
    ToolIntentDefinition("shell_command", ("bash", "shell_execute", "run_command", "execute_command"), ("command",), ("cwd",), "high", ("bash",), "admin"),
    ToolIntentDefinition("file_write", ("file_write", "file_patch"), ("path",), ("content", "patch"), "medium", ("file_write", "file_patch"), "write"),
    ToolIntentDefinition("file_read", ("file_read", "file_list"), ("path",), tuple(), "low", ("file_read", "file_list"), "read"),
    ToolIntentDefinition("web_search", ("web_search", "web_fetch"), ("query",), ("url",), "low", ("web_search", "web_fetch"), "read"),
    ToolIntentDefinition("git_query", ("git_status", "git_diff", "git_log"), tuple(), tuple(), "low", ("git_status", "git_diff", "git_log"), "read"),
    ToolIntentDefinition("git_commit", ("git_commit",), tuple(), tuple(), "high", ("git_commit",), "admin"),
)

_BY_TOOL: dict[str, ToolIntentDefinition] = {
    tool: definition
    for definition in _DEFINITIONS
    for tool in definition.tool_names
}


class ToolIntentTaxonomyService:
    def all_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "intent": d.intent,
                "tool_names": list(d.tool_names),
                "required_args": list(d.required_args),
                "optional_args": list(d.optional_args),
                "risk_class": d.risk_class,
                "allowed_remap_targets": list(d.allowed_remap_targets),
                "tool_class": d.tool_class,
            }
            for d in _DEFINITIONS
        ]

    def classify_tool(self, tool_name: str | None) -> dict[str, Any]:
        normalized = str(tool_name or "").strip()
        d = _BY_TOOL.get(normalized)
        if d is None:
            return {
                "intent": "unknown",
                "risk_class": "medium",
                "tool_class": "unknown",
                "required_args": [],
            }
        return {
            "intent": d.intent,
            "risk_class": d.risk_class,
            "tool_class": d.tool_class,
            "required_args": list(d.required_args),
        }


_service = ToolIntentTaxonomyService()


def get_tool_intent_taxonomy_service() -> ToolIntentTaxonomyService:
    return _service

