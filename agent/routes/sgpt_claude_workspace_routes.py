"""Claude Code isolated-workspace endpoints of the ``sgpt`` blueprint.

The view functions are registered on ``sgpt_bp`` by :mod:`agent.routes.sgpt`,
which keeps owning the blueprint, its URLs and the authentication decorators.
This module only translates HTTP requests into write-armed runs and reviewed
diff applies of the Claude CLI backend (SRP).
"""

from __future__ import annotations

import logging
import time

from flask import request

from agent.common.errors import api_response

audit_logger = logging.getLogger("audit")

_WORKDIR_REQUIRED = "workdir is required (git repo within claude_cli.allowed_paths)"


def claude_write_armed_run():
    """write_armed-Run fuer Claude Code: schreibt nur in eine isolierte
    Workspace-Kopie und liefert den Diff als Artefakt
    (status=awaiting_diff_review). Der Diff wird nie automatisch
    angewendet — Uebernahme ist eine manuelle Review-Entscheidung.
    """
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return api_response(status="error", message="prompt is required", code=400)
    workdir = str(body.get("workdir") or "").strip()
    if not workdir:
        return api_response(status="error", message=_WORKDIR_REQUIRED, code=400)
    model = str(body.get("model") or "").strip() or None
    try:
        timeout = int(body.get("timeout") or 600)
    except (TypeError, ValueError):
        timeout = 600
    timeout = max(30, min(timeout, 3600))

    from agent.cli_backends.opencode import run_claude_write_armed

    started = time.time()
    result = run_claude_write_armed(prompt=prompt[:4000], model=model, timeout=timeout, workdir=workdir)
    duration_ms = int((time.time() - started) * 1000)
    changed_files = len(result.get("changed_files") or [])
    audit_logger.info(
        f"Claude write_armed run: status={result.get('status')} changed_files={changed_files}",
        extra={
            "extra_fields": {
                "action": "claude_write_armed_run",
                "status": result.get("status"),
                "rc": result.get("rc"),
                "changed_files": changed_files,
                "duration_ms": duration_ms,
            }
        },
    )
    result["duration_ms"] = duration_ms
    return api_response(data=result)


def claude_apply_reviewed_diff():
    """Diff-Apply nach Review: wendet einen geprueften write_armed-Diff
    auf das Original-Workdir an (git apply --check, dann git apply).
    Es wird nicht committet — der Commit bleibt manuelle Entscheidung.
    """
    body = request.get_json(silent=True) or {}
    diff = str(body.get("diff") or "")
    if not diff.strip():
        return api_response(status="error", message="diff is required", code=400)
    workdir = str(body.get("workdir") or "").strip()
    if not workdir:
        return api_response(status="error", message=_WORKDIR_REQUIRED, code=400)

    from agent.cli_backends.opencode import apply_reviewed_diff

    result = apply_reviewed_diff(diff=diff, workdir=workdir)
    changed_files = len(result.get("changed_files") or [])
    audit_logger.info(
        f"Claude diff-apply: status={result.get('status')} changed_files={changed_files}",
        extra={
            "extra_fields": {
                "action": "claude_apply_reviewed_diff",
                "status": result.get("status"),
                "applied": bool(result.get("applied")),
                "changed_files": changed_files,
            }
        },
    )
    code = {"applied": 200, "conflict": 409}.get(result.get("status"), 422)
    return api_response(data=result, code=code)


__all__ = ["claude_apply_reviewed_diff", "claude_write_armed_run"]
