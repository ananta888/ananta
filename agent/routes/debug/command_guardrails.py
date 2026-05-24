"""SCG-013: POST /debug/command-guardrails/analyze — dry-run shell command chain analysis."""
from __future__ import annotations

import logging

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy

logger = logging.getLogger(__name__)

command_guardrails_bp = Blueprint("debug_command_guardrails", __name__)


def _is_admin() -> bool:
    try:
        remote = request.remote_addr or ""
        if remote in ("127.0.0.1", "::1", "localhost"):
            return True
        user_info = getattr(g, "user_info", None) or {}
        return str(user_info.get("role") or "").strip().lower() == "admin"
    except Exception:
        return False


@command_guardrails_bp.route("/debug/command-guardrails/analyze", methods=["POST"])
@check_auth
def analyze_command_guardrail():
    """Dry-run: analyze a shell command against the Shell Command Policy without executing it."""
    if not _is_admin():
        return api_response(status="error", message="forbidden", code=403)

    body = request.get_json(silent=True) or {}
    command = str(body.get("command") or "").strip()
    if not command:
        return api_response(status="error", message="command is required", code=400)

    agent_cfg = body.get("agent_cfg") if isinstance(body.get("agent_cfg"), dict) else None
    policy_override = body.get("policy") if isinstance(body.get("policy"), dict) else None

    if policy_override:
        policy = ShellCommandPolicy.from_config(policy_override)
        analysis = ShellCommandAnalyzer().analyze_with_policy(command, policy)
    else:
        analysis = ShellCommandAnalyzer().analyze(command, agent_cfg)

    result = analysis.as_dict()
    result["segments"] = [
        {
            "index": seg.index,
            "raw": seg.raw[:200],
            "operator_before": seg.operator_before,
            "operator_after": seg.operator_after,
        }
        for seg in (analysis.segments or [])
    ]
    result["policy_snapshot"] = analysis.policy_snapshot

    logger.info(
        "command_guardrail_analyze command_preview=%s allowed=%s segment_count=%d",
        command[:80],
        analysis.allowed,
        len(analysis.segments),
    )
    return api_response(data=result)
