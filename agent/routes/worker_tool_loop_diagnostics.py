"""AWTCL-019 / AWWPI-019: diagnostics for the ananta-worker tool/mutation loops.

The loop runtimes persist their per-run reports in the workspace
(``.ananta/tool-loop-report.json`` and ``.ananta/mutation-report.json``).
This route lists and serves those reports so the UI can show ToolCalls,
policy decisions, ToolResults, mutation modes, diffs and blocked changes
per worker run. Workspace paths are resolved strictly inside the
configured workspace root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_auth
from agent.config import settings

worker_tool_loop_diagnostics_bp = Blueprint("worker_tool_loop_diagnostics", __name__)

_REPORT_FILENAMES = {
    "tool_loop": "tool-loop-report.json",
    "mutation": "mutation-report.json",
}


def _workspace_root() -> Path:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
    raw = str(runtime_cfg.get("workspace_root") or "").strip()
    if not raw:
        raw = str(Path(settings.data_dir) / "worker-runtime")
    return Path(raw).resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except ValueError:
        return False


def _load_report(workspace_dir: Path, kind: str) -> dict[str, Any] | None:
    path = workspace_dir / ".ananta" / _REPORT_FILENAMES[kind]
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@worker_tool_loop_diagnostics_bp.get("/api/diagnostics/ananta-worker/runs")
@check_auth
def list_worker_loop_runs():
    """List workspaces under the workspace root that carry loop reports."""
    root = _workspace_root()
    rows: list[dict[str, Any]] = []
    if root.exists():
        for report_path in sorted(root.glob("**/.ananta/tool-loop-report.json")) + sorted(
            root.glob("**/.ananta/mutation-report.json")
        ):
            workspace_dir = report_path.parent.parent
            rel = str(workspace_dir.relative_to(root)).replace("\\", "/")
            kind = "tool_loop" if report_path.name == "tool-loop-report.json" else "mutation"
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "workspace": rel,
                    "kind": kind,
                    "outcome": str(payload.get("outcome") or ""),
                    "mutation_mode": str(payload.get("mutation_mode") or "") or None,
                    "task_id": payload.get("task_id"),
                    "session_id": payload.get("session_id"),
                    "created_at": payload.get("created_at"),
                    "iteration_count": len(list(payload.get("iterations") or [])),
                }
            )
    rows.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
    return jsonify({"workspace_root": str(root), "runs": rows[:200]})


@worker_tool_loop_diagnostics_bp.get("/api/diagnostics/ananta-worker/report")
@check_auth
def get_worker_loop_report():
    """Serve one full loop report (tool calls, policy decisions, evidence)."""
    root = _workspace_root()
    workspace_rel = str(request.args.get("workspace") or "").strip()
    kind = str(request.args.get("kind") or "tool_loop").strip()
    if kind not in _REPORT_FILENAMES:
        return jsonify({"error": "invalid_kind"}), 400
    if not workspace_rel:
        return jsonify({"error": "workspace_required"}), 400
    workspace_dir = (root / workspace_rel).resolve()
    if not _is_within(workspace_dir, root):
        return jsonify({"error": "workspace_outside_root"}), 400
    report = _load_report(workspace_dir, kind)
    if report is None:
        return jsonify({"error": "report_not_found"}), 404
    return jsonify({"workspace": workspace_rel, "kind": kind, "report": report})
