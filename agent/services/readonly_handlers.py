"""Read-only deterministic handlers (te-006).

Handlers: list_files, read_file, grep_search, git_status, git_diff,
          json_validate, schema_validate

Each class implements the TaskHandler protocol (propose + execute).
All handlers are safe to run without LLM involvement.

Registration is done via ``register_readonly_handlers(app)`` called from
the app factory or an init hook.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from flask import Flask

from agent.services.task_handler_registry import TaskHandler, register_task_handler


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str | None = None, timeout: int = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"


def _safe_path(raw: str, base: str | None = None) -> str:
    p = Path(raw).expanduser()
    if base and not p.is_absolute():
        p = Path(base) / p
    return str(p)


def _proposal(handler_id: str, tool_calls: list[dict]) -> dict[str, Any]:
    return {
        "proposal_id": f"{handler_id}-proposal",
        "strategy_id": "deterministic_handler",
        "tool_calls": tool_calls,
        "command": None,
        "expected_artifacts": [],
        "safety_flags": {"read_only": True},
    }


def _result(output: str, exit_code: int = 0, meta: dict | None = None) -> dict[str, Any]:
    return {"output": output, "exit_code": exit_code, **(meta or {})}


# ── list_files ────────────────────────────────────────────────────────────────

class ListFilesHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or task.get("directory") or "."
        return _proposal("list_files", [{"name": "list_files", "arguments": {"path": path}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = _safe_path(task.get("path") or task.get("directory") or ".")
        pattern = task.get("pattern") or "*"
        recursive = bool(task.get("recursive", False))

        if recursive:
            cmd = ["find", path, "-name", pattern, "-type", "f"]
        else:
            cmd = ["ls", "-1", path]
        code, out, err = _run(cmd)
        return _result(out or err, code)


# ── read_file ─────────────────────────────────────────────────────────────────

class ReadFileHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or task.get("file") or ""
        return _proposal("read_file", [{"name": "read_file", "arguments": {"path": path}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        raw = task.get("path") or task.get("file") or ""
        if not raw:
            return _result("", 1, {"error": "path_required"})
        path = _safe_path(raw)
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            return _result(content, 0)
        except FileNotFoundError:
            return _result("", 1, {"error": f"not_found: {path}"})
        except PermissionError:
            return _result("", 1, {"error": f"permission_denied: {path}"})


# ── grep_search ───────────────────────────────────────────────────────────────

class GrepSearchHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        pattern = task.get("pattern") or task.get("query") or ""
        path = task.get("path") or "."
        return _proposal("grep_search", [{"name": "grep_search", "arguments": {"pattern": pattern, "path": path}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        pattern = task.get("pattern") or task.get("query") or ""
        if not pattern:
            return _result("", 1, {"error": "pattern_required"})
        path = _safe_path(task.get("path") or ".")
        include = task.get("include") or "*"
        flags = ["-rn", "--include", include]
        if task.get("ignore_case"):
            flags.append("-i")
        code, out, err = _run(["grep"] + flags + [pattern, path])
        return _result(out or err, code)


# ── git_status ────────────────────────────────────────────────────────────────

class GitStatusHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        cwd = task.get("cwd") or "."
        return _proposal("git_status", [{"name": "git_status", "arguments": {"cwd": cwd}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        cwd = task.get("cwd") or "."
        code, out, err = _run(["git", "status", "--short"], cwd=cwd)
        return _result(out or err, code)


# ── git_diff ──────────────────────────────────────────────────────────────────

class GitDiffHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        ref = task.get("ref") or "HEAD"
        cwd = task.get("cwd") or "."
        return _proposal("git_diff", [{"name": "git_diff", "arguments": {"ref": ref, "cwd": cwd}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        cwd = task.get("cwd") or "."
        ref = task.get("ref") or ""
        cmd = ["git", "diff"]
        if ref:
            cmd.append(ref)
        code, out, err = _run(cmd, cwd=cwd)
        return _result(out or err, code)


# ── json_validate ─────────────────────────────────────────────────────────────

class JsonValidateHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or ""
        return _proposal("json_validate", [{"name": "json_validate", "arguments": {"path": path}}])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or ""
        content = task.get("content") or ""
        if path:
            try:
                content = Path(_safe_path(path)).read_text(encoding="utf-8")
            except OSError as e:
                return _result("", 1, {"error": str(e)})
        if not content:
            return _result("", 1, {"error": "no_content_or_path"})
        try:
            json.loads(content)
            return _result("valid", 0, {"valid": True})
        except json.JSONDecodeError as e:
            return _result(str(e), 1, {"valid": False, "error": str(e)})


# ── schema_validate ───────────────────────────────────────────────────────────

class SchemaValidateHandler:
    def propose(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or ""
        schema_path = task.get("schema_path") or ""
        return _proposal("schema_validate", [
            {"name": "schema_validate", "arguments": {"path": path, "schema_path": schema_path}},
        ])

    def execute(self, **kwargs: Any) -> dict:
        task = kwargs.get("task") or {}
        path = task.get("path") or ""
        schema_path = task.get("schema_path") or ""
        content_str = task.get("content") or ""
        schema_str = task.get("schema") or ""

        if path:
            try:
                content_str = Path(_safe_path(path)).read_text(encoding="utf-8")
            except OSError as e:
                return _result("", 1, {"error": str(e)})
        if schema_path:
            try:
                schema_str = Path(_safe_path(schema_path)).read_text(encoding="utf-8")
            except OSError as e:
                return _result("", 1, {"error": str(e)})

        if not content_str or not schema_str:
            return _result("", 1, {"error": "content_and_schema_required"})

        try:
            import jsonschema  # optional dep
        except ImportError:
            return _result("", 1, {"error": "jsonschema_not_installed"})

        try:
            instance = json.loads(content_str)
            schema = json.loads(schema_str)
            jsonschema.validate(instance, schema)
            return _result("valid", 0, {"valid": True})
        except json.JSONDecodeError as e:
            return _result(str(e), 1, {"valid": False, "error": f"json_parse:{e}"})
        except jsonschema.ValidationError as e:
            return _result(e.message, 1, {"valid": False, "error": e.message})


# ── registration ──────────────────────────────────────────────────────────────

_HANDLERS: dict[str, TaskHandler] = {
    "list_files":      ListFilesHandler(),
    "read_file":       ReadFileHandler(),
    "grep_search":     GrepSearchHandler(),
    "git_status":      GitStatusHandler(),
    "git_diff":        GitDiffHandler(),
    "json_validate":   JsonValidateHandler(),
    "schema_validate": SchemaValidateHandler(),
}


def register_readonly_handlers(app: Flask | None = None) -> None:
    """Register all read-only handlers into the TaskHandlerRegistry."""
    for task_kind, handler in _HANDLERS.items():
        register_task_handler(
            task_kind,
            handler,
            app,
            capabilities=["read_only"],
            safety_flags={"read_only": True, "mutates_filesystem": False},
        )
