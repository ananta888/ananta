"""POST /api/snapshot/diff — stateless endpoint that diffs two compact DOM
snapshots. Used by the Angular UiSnapshotService when it wants to render
delta-only log entries without computing the diff locally."""
from __future__ import annotations

from typing import Any
from flask import Blueprint, jsonify, request

from agent.services.snapshot_delta import diff_snapshots

snapshot_diff_bp = Blueprint("snapshot_diff_api", __name__, url_prefix="/api/snapshot")


@snapshot_diff_bp.route("/diff", methods=["POST"])
def diff_snapshots_endpoint():
    """Body: {"prev": "<compact snapshot>", "curr": "<compact snapshot>"}.

    Response: {"lines": [..], "changed_paths": [..], "is_empty": bool}
    The frontend uses this to render delta-only entries in the visual
    snake log when predictive_guide_log_deltas_only is on.
    """
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    prev = str(body.get("prev", "") or "")
    curr = str(body.get("curr", "") or "")
    if not body or "prev" not in body or "curr" not in body:
        return jsonify({"error": "prev and curr required"}), 400
    delta = diff_snapshots(prev, curr)
    return jsonify({
        "lines": delta.lines,
        "changed_paths": delta.changed_paths,
        "is_empty": delta.is_empty(),
    })
