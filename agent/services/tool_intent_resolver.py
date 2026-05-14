from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.services.task_execution_policy_service import normalize_tool_call_name


@dataclass(frozen=True)
class ToolIntentRemapEvent:
    original_tool: str
    resolved_tool: str
    reason: str
    confidence: str = "explicit_rule"


@dataclass(frozen=True)
class ToolIntentUnresolved:
    original_tool: str
    reason_code: str


@dataclass(frozen=True)
class ToolIntentResolution:
    resolved_tool_calls: list[dict[str, Any]]
    remap_events: list[ToolIntentRemapEvent]
    unresolved: list[ToolIntentUnresolved]


class ToolIntentResolver:
    """Conservative default-path tool intent resolver.

    Unknown tools are never auto-converted to shell execution by default.
    """

    _KNOWN_DIRECT = {
        "bash",
        "shell_execute",
        "run_command",
        "execute_command",
        "file_write",
        "file_read",
        "file_list",
        "file_patch",
        "web_search",
        "web_fetch",
        "git_status",
        "git_diff",
        "git_log",
        "git_commit",
    }

    def resolve(self, tool_calls: list[dict] | None, *, known_tools: list[str] | set[str] | tuple[str, ...] | None) -> ToolIntentResolution:
        known = {str(item).strip() for item in (known_tools or []) if str(item).strip()}
        remapped: list[dict[str, Any]] = []
        events: list[ToolIntentRemapEvent] = []
        unresolved: list[ToolIntentUnresolved] = []

        for item in list(tool_calls or []):
            if not isinstance(item, dict):
                unresolved.append(ToolIntentUnresolved(original_tool="<invalid>", reason_code="invalid_tool_call"))
                continue

            tc = dict(item)
            raw_name = str(tc.get("name") or tc.get("tool_name") or tc.get("function_name") or "").strip()
            args = tc.get("args") or tc.get("tool_input") or tc.get("parameters") or tc.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}

            canonical = normalize_tool_call_name(raw_name)
            if not canonical:
                unresolved.append(ToolIntentUnresolved(original_tool="<missing>", reason_code="missing_tool_name"))
                continue

            command_payload = str(args.get("command") or args.get("cmd") or "").strip()
            path_payload = str(args.get("path") or args.get("file_path") or args.get("filename") or "").strip()
            content_payload = (
                str(args.get("content") or "").strip()
                or str(args.get("text") or "").strip()
                or str(args.get("details") or "").strip()
                or str(args.get("summary") or "").strip()
            )

            resolved_name = canonical
            resolved_args = dict(args)
            reason = "canonical"

            if canonical not in self._KNOWN_DIRECT and known and canonical not in known:
                if path_payload and content_payload:
                    resolved_name = "file_write"
                    resolved_args = {"path": path_payload, "content": content_payload}
                    reason = "path_plus_content_to_file_write"
                elif path_payload and not content_payload:
                    resolved_name = "file_read"
                    resolved_args = {"path": path_payload}
                    reason = "path_only_to_file_read"
                elif command_payload:
                    unresolved.append(
                        ToolIntentUnresolved(
                            original_tool=raw_name or canonical,
                            reason_code="unknown_tool_command_payload_requires_explicit_shell",
                        )
                    )
                    continue
                else:
                    unresolved.append(ToolIntentUnresolved(original_tool=raw_name or canonical, reason_code="unknown_tool"))
                    continue

            if "command" not in resolved_args and resolved_args.get("cmd"):
                resolved_args["command"] = resolved_args.get("cmd")

            tc["name"] = resolved_name
            tc["tool_name"] = resolved_name
            tc["args"] = resolved_args
            remapped.append(tc)

            if reason != "canonical" or raw_name != resolved_name:
                events.append(
                    ToolIntentRemapEvent(
                        original_tool=raw_name or "<missing>",
                        resolved_tool=resolved_name,
                        reason=reason,
                    )
                )

        return ToolIntentResolution(
            resolved_tool_calls=remapped,
            remap_events=events,
            unresolved=unresolved,
        )
