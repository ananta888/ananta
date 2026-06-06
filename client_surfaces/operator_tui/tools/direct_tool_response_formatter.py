"""SCTR-008: compact formatting for direct SnakeChat tool responses."""
from __future__ import annotations

from typing import Any


def format_direct_tool_response(route: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact user-facing payload while preserving raw structured data."""
    if not result.get("ok"):
        return {
            "route": route,
            "route_source": "direct_tool",
            "text": f"Tool error: {result.get('error') or 'unknown_error'}",
            "raw": result,
        }
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if route == "filesystem_read":
        text = _format_filesystem(data)
    elif route == "git_read":
        text = _format_git(data)
    elif route == "todo_read":
        text = _format_todo(data)
    else:
        text = "Tool result available."
    return {
        "route": route,
        "route_source": "direct_tool",
        "text": text,
        "raw": result,
    }


def _format_filesystem(data: dict[str, Any]) -> str:
    entries = list(data.get("entries") or [])
    if entries:
        lines = [f"{'[dir]' if item.get('is_dir') else '[file]'} {item.get('path')}" for item in entries[:40]]
        return "\n".join(lines)
    paths = list(data.get("paths") or [])
    if paths:
        return "\n".join(str(path) for path in paths[:80])
    content = str(data.get("content") or "")
    return content[:4000] if content else "No files found."


def _format_git(data: dict[str, Any]) -> str:
    if "changed_files" in data:
        branch = str(data.get("branch") or "").strip() or "unknown"
        files = list(data.get("changed_files") or [])
        if not files:
            return f"Branch: {branch}\nNo changed files."
        lines = [f"Branch: {branch}", "Changed files:"]
        lines.extend(f"- {item.get('status')}: {item.get('path')}" for item in files[:40])
        return "\n".join(lines)
    commits = list(data.get("commits") or [])
    if commits:
        return "\n".join(f"{item.get('sha')} {item.get('subject')}" for item in commits[:20])
    return "No git data available."


def _format_todo(data: dict[str, Any]) -> str:
    tracks = list(data.get("tracks") or [])
    if tracks:
        return "\n".join(
            f"{item.get('track')}: {item.get('done')}/{item.get('total')} done, {item.get('todo')} todo"
            for item in tracks[:40]
        )
    tasks = list(data.get("tasks") or [])
    if tasks:
        return "\n".join(
            f"{item.get('id')} [{item.get('status')}]: {item.get('title')}"
            for item in tasks[:60]
        )
    task = data.get("task") if isinstance(data.get("task"), dict) else None
    if task:
        return f"{task.get('id')} [{task.get('status')}]: {task.get('title')}"
    return "No todos found."
