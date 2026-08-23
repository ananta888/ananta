"""Bounded hierarchical CodeCompass architecture prefill for AI-Snake."""
from __future__ import annotations

from typing import Any, Callable


class SnakeCodeCompassArchitectureContextService:
    """Projects the existing architecture tool result into prompt context."""

    def build(
        self,
        query: str,
        *,
        max_chars: int = 6000,
        loader: Callable[..., dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if not str(query or "").strip():
            return "", {"status": "skipped", "reason": "empty_query"}
        if loader is None:
            from agent.services.tools.codecompass_architecture_tools import (
                codecompass_architecture_overview,
            )

            loader = codecompass_architecture_overview
        try:
            result = loader(
                workspace_dir=".",
                arguments={"query": query, "profile": "overview"},
                tool_call_id="snake-architecture-prefill",
            )
        except Exception as exc:
            return "", {"status": "degraded", "reason": str(exc)[:160]}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return "", {
                "status": "degraded",
                "reason": str((result or {}).get("error") or "architecture_graph_unavailable"),
                "warnings": list((result or {}).get("warnings") or []),
            }
        architecture = ((result.get("data") or {}).get("architecture") or {})
        nodes = [item for item in architecture.get("nodes") or [] if isinstance(item, dict)]
        if not nodes:
            return "", {"status": "degraded", "reason": "empty_architecture_slice"}
        rows = ["=== CodeCompass Hierarchischer Architektur-Kontext ==="]
        for item in nodes:
            level = str(item.get("level") or "unknown")
            title = str(item.get("title") or item.get("id") or "unknown")
            path = str(item.get("path") or "")
            summary = str(item.get("short_summary") or "summary_unavailable")
            handle = str(item.get("handle") or "")
            suffix = f" | path: {path}" if path else ""
            rows.append(f"- [{level}] {title}{suffix}\n  {summary}\n  handle: {handle}")
        text = "\n".join(rows)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n[Architektur-Kontext budgetbedingt gekuerzt]"
        return text, {
            "status": "ok",
            "schema": architecture.get("schema"),
            "node_count": len(nodes),
            "edge_count": len(architecture.get("edges") or []),
            "levels": list(architecture.get("levels") or []),
            "truncated": bool(architecture.get("truncated")) or len(text) >= max_chars,
            "warnings": list(architecture.get("warnings") or []),
        }


_SERVICE = SnakeCodeCompassArchitectureContextService()


def get_snake_codecompass_architecture_context_service() -> SnakeCodeCompassArchitectureContextService:
    return _SERVICE
