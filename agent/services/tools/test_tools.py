"""AWTCL-015 / AWWPI-012: allowlisted test tools for the ananta-worker loop.

``test.discover`` lists test files without executing anything.
``test.run`` only runs commands that are explicitly allowlisted in
``ananta_worker_workspace_mutation.allowlisted_test_commands``; timeout
and output limits always apply and the command runs with
``cwd=workspace``. Missing allowlist entries reject the request — they
never fall back to arbitrary execution.
"""
from __future__ import annotations

import fnmatch
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.services.tools._evidence import (
    EVIDENCE_KIND_TEST_OUTPUT,
    build_evidence_entry,
    build_tool_result,
)
from agent.services.tools.repo_tools import _iter_files

_TEST_FILE_GLOBS = ("test_*.py", "*_test.py", "*.spec.ts", "*.test.ts", "*.test.js")
_MAX_DISCOVER = 200


def test_discover(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    limit = max(1, min(int((arguments or {}).get("limit") or 100), _MAX_DISCOVER))
    root = Path(workspace_dir).resolve()
    rows: list[str] = []
    truncated = False
    for _, rel in _iter_files(root):
        name = Path(rel).name
        if not any(fnmatch.fnmatch(name, glob) for glob in _TEST_FILE_GLOBS):
            continue
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(rel)
    entry, _ = build_evidence_entry(kind="file_list", path=".", excerpt="\n".join(rows), max_excerpt_chars=6000)
    return build_tool_result(
        tool_name="test.discover",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=[entry],
        data={"test_file_count": len(rows), "truncated": truncated},
        warnings=([] if rows else ["no_tests_found"]),
    )


def _normalize_command(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def test_run(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(config or {})
    allowlist = [_normalize_command(item) for item in list(cfg.get("allowlisted_test_commands") or []) if _normalize_command(item)]
    timeout_seconds = max(5, min(int(cfg.get("test_timeout_seconds") or 120), 1800))
    output_max_chars = max(200, min(int(cfg.get("test_output_max_chars") or 4000), 40000))
    command = _normalize_command(str((arguments or {}).get("command") or ""))
    if not command:
        return build_tool_result(tool_name="test.run", tool_call_id=tool_call_id, status="error", error="command_required")
    if command not in allowlist:
        return build_tool_result(
            tool_name="test.run",
            tool_call_id=tool_call_id,
            status="policy_blocked",
            error="command_not_allowlisted",
            risk_class="execution",
            warnings=["test_command_not_allowlisted"],
        )
    argv = shlex.split(command)
    started = time.time()
    try:
        result = subprocess.run(  # noqa: S603 - command is hub-allowlisted, no shell
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=str(workspace_dir),
        )
        rc, stdout, stderr = result.returncode, result.stdout, result.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        rc, stdout, stderr = -1, str(exc.stdout or ""), str(exc.stderr or "")
        timed_out = True
    except OSError as exc:
        return build_tool_result(tool_name="test.run", tool_call_id=tool_call_id, status="error", error=str(exc))
    duration = round(time.time() - started, 3)
    stdout_excerpt = stdout[-output_max_chars:]
    stderr_excerpt = stderr[-output_max_chars:]
    entry, _ = build_evidence_entry(
        kind=EVIDENCE_KIND_TEST_OUTPUT,
        path=command,
        excerpt=(stdout_excerpt + ("\n--- stderr ---\n" + stderr_excerpt if stderr_excerpt else "")).strip(),
        max_excerpt_chars=output_max_chars,
    )
    warnings = ["test_timeout"] if timed_out else []
    return build_tool_result(
        tool_name="test.run",
        tool_call_id=tool_call_id,
        status="ok" if rc == 0 else "test_failed",
        risk_class="execution",
        evidence=[entry],
        data={
            "command": command,
            "rc": rc,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
            "duration_seconds": duration,
            "timed_out": timed_out,
        },
        warnings=warnings,
    )
