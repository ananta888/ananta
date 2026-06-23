"""Flask API for the Three-Way Flex Diff / AI Mode (T01)."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from flask import Blueprint, jsonify, request

from client_surfaces.operator_tui.diff.ai_diff_dispatch import dispatch_ai_diff_request
from client_surfaces.operator_tui.diff.ai_diff_panel_state import (
    build_ai_diff_panel_state,
    set_ai_diff_mode,
)
from client_surfaces.operator_tui.diff.diff_sources import (
    build_current_diff_source_ref,
    build_file_view_source_ref,
    build_output_artifact_source_ref,
)
from client_surfaces.operator_tui.diff.three_way_diff_state import (
    build_three_way_diff_session,
    set_panel_state,
    validate_three_way_diff_session,
)

_log = logging.getLogger(__name__)


def _repo_root() -> Path:
    try:
        from agent.config import settings as _s
        rr = getattr(_s, "rag_repo_root", None)
        if rr:
            return Path(str(rr)).resolve()
    except Exception:
        pass
    return Path(".").resolve()

diff3_bp = Blueprint("diff3", __name__)

# In-memory session store for the lifetime of the Flask process.
# Production would use Redis or DB; for the PoC this is sufficient.
_SESSIONS: dict[str, dict] = {}

_VALID_AI_MODES = {"review", "explain", "risk", "tests", "patch", "chat"}
_VALID_PANEL_IDS = {"A", "B", "C"}
_VALID_LAYOUT_MODES = {"equal", "focus", "compact", "left-wide", "right-wide", "focus-a", "focus-b", "focus-c"}


# ── Session management ────────────────────────────────────────────────────────

@diff3_bp.route("/api/diff3/sessions", methods=["POST"])
def create_session():
    """Create a new three-way diff session.

    Body (optional JSON):
      goal_id: str
      layout_mode: "equal" | "left-wide" | "right-wide"
      session_id: str  (auto-generated if omitted)
    """
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id") or uuid.uuid4().hex[:12])
    goal_id = str(body.get("goal_id") or "").strip() or None
    layout_mode = str(body.get("layout_mode") or "equal")
    if layout_mode not in _VALID_LAYOUT_MODES:
        layout_mode = "equal"

    try:
        session = build_three_way_diff_session(
            session_id=session_id,
            goal_id=goal_id,
            layout_mode=layout_mode,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Auto-populate panel A with the current git diff
    try:
        source_left = build_current_diff_source_ref(path_filter="")
        session = set_panel_state(
            session,
            panel_id="A",
            panel_type="diff",
            source_left=source_left,
            source_right=None,
            render_mode="unified",
        )
    except Exception as exc:
        _log.debug("diff3: could not auto-populate panel A: %s", exc)

    _SESSIONS[session_id] = session
    return jsonify(session), 201


@diff3_bp.route("/api/diff3/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify(session)


@diff3_bp.route("/api/diff3/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    _SESSIONS.pop(session_id, None)
    return jsonify({"ok": True})


# ── Panel configuration ───────────────────────────────────────────────────────

@diff3_bp.route("/api/diff3/sessions/<session_id>/panels/<panel_id>", methods=["PUT"])
def update_panel(session_id: str, panel_id: str):
    """Configure a panel source.

    Body:
      source_kind: "current_diff" | "output_artifact" | "ai" | "empty"
      render_mode: "unified" | "summary" | "ai_review" | "ai_chat"
      output_artifact_id: str  (only for source_kind=output_artifact)
      goal_id: str             (context for output_artifact)
      ai_mode: str             (only for source_kind=ai)
    """
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404

    pid = panel_id.upper()
    if pid not in _VALID_PANEL_IDS:
        return jsonify({"error": "invalid_panel_id"}), 400

    body = request.get_json(silent=True) or {}
    source_kind = str(body.get("source_kind") or "empty").lower()
    render_mode = str(body.get("render_mode") or "unified").lower()
    goal_id = str(body.get("goal_id") or "").strip() or None

    source_left = None
    panel_type = "diff"

    if source_kind == "current_diff":
        source_left = build_current_diff_source_ref(
            path_filter=str(body.get("path_filter") or "")
        )
        panel_type = "diff"
    elif source_kind == "file_content":
        file_path = str(body.get("path") or body.get("path_filter") or "").strip()
        if not file_path:
            return jsonify({"error": "path required for file_content"}), 400
        source_left = build_file_view_source_ref(path=file_path)
        panel_type = "file_view"
        render_mode = "full_file"
    elif source_kind == "output_artifact":
        output_id = str(body.get("output_artifact_id") or "").strip()
        if not output_id:
            return jsonify({"error": "output_artifact_id required"}), 400
        source_left = build_output_artifact_source_ref(
            output_artifact_id=output_id, goal_id=goal_id
        )
        panel_type = "diff"
    elif source_kind == "ai":
        ai_mode = str(body.get("ai_mode") or "review").lower()
        if ai_mode not in _VALID_AI_MODES:
            return jsonify({"error": f"invalid ai_mode: {ai_mode}"}), 400
        panel_type = "ai_review" if ai_mode != "patch" else "ai_patch"
        render_mode = "ai_chat" if ai_mode == "chat" else "ai_review"
        ai_state = build_ai_diff_panel_state(
            mode=ai_mode, selected_panels=["A", "B"], status="idle"
        )
        session["extensions"] = dict(session.get("extensions") or {})
        session["extensions"]["ai_panel_state"] = ai_state
    else:
        panel_type = "empty"
        render_mode = "unified"

    try:
        updated = set_panel_state(
            session,
            panel_id=pid,
            panel_type=panel_type,
            source_left=source_left,
            source_right=None,
            render_mode=render_mode,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    _SESSIONS[session_id] = updated
    return jsonify(updated)


@diff3_bp.route("/api/diff3/sessions/<session_id>/focus", methods=["PUT"])
def set_focus(session_id: str):
    """Set active panel focus. Body: { "panel_id": "A"|"B"|"C" }"""
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    body = request.get_json(silent=True) or {}
    pid = str(body.get("panel_id") or "A").upper()
    if pid not in _VALID_PANEL_IDS:
        return jsonify({"error": "invalid_panel_id"}), 400
    session["active_panel"] = pid
    from datetime import UTC, datetime
    session["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _SESSIONS[session_id] = session
    return jsonify(session)


@diff3_bp.route("/api/diff3/sessions/<session_id>/layout", methods=["PUT"])
def set_layout(session_id: str):
    """Set layout mode. Body: { "layout_mode": "equal"|"left-wide"|... }"""
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    body = request.get_json(silent=True) or {}
    mode = str(body.get("layout_mode") or "equal")
    if mode not in _VALID_LAYOUT_MODES:
        return jsonify({"error": "invalid_layout_mode"}), 400
    session["layout_mode"] = mode
    from datetime import UTC, datetime
    session["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _SESSIONS[session_id] = session
    return jsonify(session)


@diff3_bp.route("/api/diff3/sessions/<session_id>/sync", methods=["PUT"])
def set_sync(session_id: str):
    """Set sync_scroll. Body: { "sync": true|false }"""
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    body = request.get_json(silent=True) or {}
    sync = bool(body.get("sync", False))
    extensions = dict(session.get("extensions") or {})
    extensions["sync_scroll"] = sync
    session["extensions"] = extensions
    _SESSIONS[session_id] = session
    return jsonify(session)


# ── AI dispatch ───────────────────────────────────────────────────────────────

@diff3_bp.route("/api/diff3/sessions/<session_id>/ai/run", methods=["POST"])
def run_ai(session_id: str):
    """Dispatch AI analysis against current session panels.

    Body:
      mode: "review" | "explain" | "risk" | "tests" | "patch" | "chat"
      goal_id: str  (optional)
    """
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404

    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "review").lower()
    goal_id = str(body.get("goal_id") or "").strip() or None

    if mode not in _VALID_AI_MODES:
        return jsonify({"error": f"invalid mode: {mode}"}), 400

    extensions = dict(session.get("extensions") or {})
    current_ai = extensions.get("ai_panel_state")
    running_state = (
        set_ai_diff_mode(current_ai, mode=mode, status="running")
        if isinstance(current_ai, dict)
        else build_ai_diff_panel_state(mode=mode, selected_panels=["A", "B"], status="running")
    )
    extensions["ai_panel_state"] = running_state
    session["extensions"] = extensions

    try:
        result = dispatch_ai_diff_request(
            goal_id=goal_id,
            diff3_state=session,
            mode=mode,
        )
        status = str(result.get("status") or "degraded")
    except Exception as exc:
        result = {
            "status": "degraded",
            "reason_code": "dispatch_failed",
            "response": {
                "schema": "ai_diff_response.v1",
                "status": "degraded",
                "artifact_type": mode,
                "summary": f"Dispatch failed: {exc}",
                "findings": [],
                "risks": [],
                "suggested_tests": [],
                "patch_suggestions": [],
                "source_refs": [],
                "reason_code": "dispatch_failed",
            },
            "context_envelope": {},
            "provenance_id": "",
            "output_artifact_id": "",
        }
        status = "degraded"

    import hashlib, json as _json
    completed_state = set_ai_diff_mode(
        running_state, mode=mode,
        status="degraded" if status != "success" else "completed",
    )
    completed_state["last_response_ref"] = str(
        result.get("output_artifact_id") or result.get("provenance_id") or ""
    )
    completed_state["context_refs"] = [
        f"ctx:{hashlib.sha1(_json.dumps(result.get('context_envelope') or {}, sort_keys=True).encode()).hexdigest()[:12]}"
    ]
    extensions["ai_panel_state"] = completed_state
    extensions["ai_last_response"] = dict(result.get("response") or {})
    extensions["ai_last_context"] = dict(result.get("context_envelope") or {})
    extensions["ai_last_findings"] = list(
        (result.get("response") or {}).get("findings") or []
    )
    session["extensions"] = extensions
    _SESSIONS[session_id] = session

    return jsonify({
        "session": session,
        "ai_result": result,
    })


# ── Panel content resolution ─────────────────────────────────────────────────

@diff3_bp.route("/api/diff3/sessions/<session_id>/panels/<panel_id>/content", methods=["GET"])
def get_panel_content(session_id: str, panel_id: str):
    """Resolve a panel's source_left to its actual content via DiffSourceResolver."""
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    pid = panel_id.upper()
    if pid not in _VALID_PANEL_IDS:
        return jsonify({"error": "invalid_panel_id"}), 400

    panels = list(session.get("panels") or [])
    panel = next((p for p in panels if p.get("panel_id") == pid), None)
    if not panel:
        return jsonify({"ok": False, "reason_code": "panel_not_found"}), 200

    source_ref = panel.get("source_left")
    if not source_ref:
        return jsonify({"ok": False, "reason_code": "no_source"}), 200

    try:
        from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver
        resolver = DiffSourceResolver(repo_root=_repo_root())
        result = resolver.resolve(source_ref, goal_id=session.get("goal_id"))
    except Exception as exc:
        _log.warning("diff3: content resolve failed for %s/%s: %s", session_id, pid, exc)
        result = {"ok": False, "reason_code": f"resolver_error: {exc}"}

    return jsonify(result)


@diff3_bp.route("/api/diff3/sessions/<session_id>/ai/mode", methods=["PUT"])
def set_ai_mode(session_id: str):
    """Switch AI mode without running. Body: { "mode": "review"|... }"""
    session = _SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "review").lower()
    if mode not in _VALID_AI_MODES:
        return jsonify({"error": f"invalid mode: {mode}"}), 400

    extensions = dict(session.get("extensions") or {})
    current_ai = extensions.get("ai_panel_state")
    if isinstance(current_ai, dict):
        extensions["ai_panel_state"] = set_ai_diff_mode(current_ai, mode=mode, status="idle")
    else:
        extensions["ai_panel_state"] = build_ai_diff_panel_state(
            mode=mode, selected_panels=["A", "B"], status="idle"
        )
    session["extensions"] = extensions
    _SESSIONS[session_id] = session
    return jsonify(session)
