from __future__ import annotations

import re
from dataclasses import dataclass
import json
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
                heuristic = self._heuristic_unknown_tool_resolution(raw_name=raw_name or canonical, args=args)
                if heuristic is not None:
                    resolved_name, resolved_args, reason = heuristic
                elif path_payload and content_payload:
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
                    slug = re.sub(r"[^a-z0-9_\\-]+", "_", (raw_name or canonical).lower()).strip("_") or "tool_note"
                    rendered = json.dumps(args or {}, ensure_ascii=False, indent=2)
                    resolved_name = "file_write"
                    resolved_args = {
                        "path": f"{slug}.md",
                        "content": f"# {slug}\n\n```json\n{rendered}\n```\n",
                    }
                    reason = "fallback_unknown_to_file_write"

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

    @staticmethod
    def _heuristic_unknown_tool_resolution(*, raw_name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
        name = str(raw_name or "").strip().lower()
        topics = args.get("topics") if isinstance(args.get("topics"), list) else []
        scope_elements = args.get("scope_elements") if isinstance(args.get("scope_elements"), list) else []
        nested_args = args.get("args") if isinstance(args.get("args"), dict) else {}
        text = str(
            args.get("input_text")
            or args.get("text")
            or args.get("query")
            or args.get("goal")
            or nested_args.get("input_text")
            or nested_args.get("text")
            or nested_args.get("query")
            or nested_args.get("goal")
            or ""
        ).strip()

        if "scope" in name or "summar" in name:
            lines = [str(item).strip() for item in list(scope_elements or topics) if str(item).strip()]
            body = "Project scope summary\n"
            if lines:
                body += "\n" + "\n".join(f"- {line}" for line in lines)
            elif text:
                body += f"\n\n{text}"
            return "file_write", {"path": "PROJECT_SCOPE.md", "content": body}, "heuristic_scope_to_file_write"

        if "google" in name or "search" in name:
            query = text or "project scope definition"
            return "web_search", {"query": query}, "heuristic_search_to_web_search"

        planning_tokens = (
            "generate",
            "create",
            "define",
            "declare",
            "plan",
            "draft",
            "outline",
            "blueprint",
            "scope",
            "backlog",
            "governance",
        )
        if any(token in name for token in planning_tokens):
            slug = re.sub(r"[^a-z0-9_\\-]+", "_", name).strip("_") or "plan_note"
            rendered = json.dumps(nested_args or args or {}, ensure_ascii=False, indent=2)
            content = f"# {slug}\n\n```json\n{rendered}\n```\n"
            return "file_write", {"path": f"{slug}.md", "content": content}, "heuristic_planning_to_file_write"

        if "architect" in name or "design" in name:
            slug = re.sub(r"[^a-z0-9_\\-]+", "_", name).strip("_") or "architecture_blueprint"
            rendered = json.dumps(nested_args or args or {}, ensure_ascii=False, indent=2)
            content = (
                f"# {slug}\n\n"
                "## Goal\n\n"
                f"{text or 'Architecture blueprint artifact.'}\n\n"
                "## Inputs\n\n"
                f"```json\n{rendered}\n```\n"
            )
            return "file_write", {"path": f"{slug}.md", "content": content}, "heuristic_architecture_to_file_write"

        if name.startswith("tool_") or ":tool_" in name:
            slug = re.sub(r"[^a-z0-9_\\-]+", "_", name).strip("_") or "tool_note"
            rendered = json.dumps(nested_args or args or {}, ensure_ascii=False, indent=2)
            content = f"# {slug}\n\n```json\n{rendered}\n```\n"
            return "file_write", {"path": f"{slug}.md", "content": content}, "heuristic_generic_tool_to_file_write"

        return None
