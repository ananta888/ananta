"""Task triggers HTTP surface (CLN-048): thin handlers over the engine."""

from __future__ import annotations

import json
import logging

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.models import TriggerConfigureRequest, TriggerTestRequest
from agent.services.autopilot.trigger_engine import (
    TRIGGERS_CONFIG_KEY,
    TriggerEngine,
    _repos,
    trigger_engine,
)
from agent.services.service_registry import get_core_services
from agent.utils import validate_request

triggers_bp = Blueprint("triggers", __name__)


def init_triggers():
    try:
        cfg = _repos().config_repo.get_by_key(TRIGGERS_CONFIG_KEY)
        if cfg:
            data = json.loads(cfg.value_json or "{}")
            trigger_engine.configure(
                enabled_sources=data.get("enabled_sources"),
                webhook_secrets=data.get("webhook_secrets"),
                auto_start_planner=data.get("auto_start_planner"),
                ip_whitelists=data.get("ip_whitelists"),
                rate_limits=data.get("rate_limits"),
                dedup_enabled=data.get("dedup_enabled"),
                dedup_ttl_seconds=data.get("dedup_ttl_seconds"),
                replay_window_seconds=data.get("replay_window_seconds"),
            )
            logging.info("Triggers configuration loaded")
    except Exception as e:
        logging.warning(f"Could not load triggers config: {e}")

    trigger_engine.register_handler("generic", lambda p, h: trigger_engine._default_handler("generic", p))
    trigger_engine.register_handler(
        "github",
        lambda p, h: trigger_engine._handle_github_issue(p) if "issue" in p else trigger_engine._handle_github_pr(p),
    )
    trigger_engine.register_handler(
        "slack",
        lambda p, h: trigger_engine._handle_slack_event(p),
    )
    trigger_engine.register_handler(
        "jira",
        lambda p, h: trigger_engine._handle_jira_event(p),
    )
    trigger_engine.register_handler(
        "email",
        lambda p, h: trigger_engine._handle_email_event(p),
    )
    trigger_engine.enable_source("generic")
    trigger_engine.enable_source("github")
    trigger_engine.enable_source("slack")
    trigger_engine.enable_source("jira")
    trigger_engine.enable_source("email")


@triggers_bp.route("/triggers/status", methods=["GET"])
@check_auth
def triggers_status():
    return api_response(data=get_core_services().trigger_runtime_service.status())


@triggers_bp.route("/triggers/configure", methods=["POST"])
@check_auth
@admin_required
@validate_request(TriggerConfigureRequest)
def triggers_configure():
    data = g.validated_data
    new_config = get_core_services().trigger_runtime_service.configure(
        enabled_sources=data.enabled_sources,
        webhook_secrets=data.webhook_secrets,
        auto_start_planner=data.auto_start_planner,
        ip_whitelists=data.ip_whitelists,
        rate_limits=data.rate_limits,
        dedup_enabled=data.dedup_enabled,
        dedup_ttl_seconds=data.dedup_ttl_seconds,
        replay_window_seconds=data.replay_window_seconds,
        persist_key=TRIGGERS_CONFIG_KEY,
    )
    return api_response(data=new_config)


def _get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@triggers_bp.route("/triggers/webhook/<source>", methods=["POST"])
def webhook_endpoint(source: str):
    """
    Empfängt Webhooks von externen Quellen und erstellt Tasks.

    Unterstützte Sources:
    - generic: Akzeptiert beliebige JSON-Payloads mit 'tasks', 'description' oder 'title'
    - github: GitHub Webhooks (Issues, PRs)

    Header:
    - X-Hub-Signature-256: HMAC-SHA256 Signatur (optional, wenn secret konfiguriert)
    """
    payload_raw = request.get_data()
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return api_response(status="error", message="invalid_json", code=400)

    signature = request.headers.get("X-Hub-Signature-256", "")
    client_ip = _get_client_ip()

    if not get_core_services().trigger_runtime_service.verify_signature(
        source=source, payload_raw=payload_raw, signature=signature
    ):
        log_audit("trigger_webhook_rejected", {"source": source, "reason": "invalid_signature", "ip": client_ip})
        return api_response(status="error", message="invalid_signature", code=401)

    result = get_core_services().trigger_runtime_service.process_webhook(
        source=source,
        payload=payload,
        headers=dict(request.headers),
        client_ip=client_ip,
    )

    if result.get("status") == "disabled":
        log_audit("trigger_webhook_rejected", {"source": source, "reason": "source_disabled", "ip": client_ip})
        return api_response(status="error", message="source_disabled", code=403)
    if result.get("status") == "ip_blocked":
        log_audit("trigger_webhook_rejected", {"source": source, "reason": "ip_not_whitelisted", "ip": client_ip})
        return api_response(status="error", message="ip_not_whitelisted", code=403)
    if result.get("status") == "rate_limited":
        log_audit("trigger_webhook_rejected", {"source": source, "reason": "rate_limit_exceeded", "ip": client_ip})
        return api_response(status="error", message="rate_limit_exceeded", code=429)
    if result.get("status") == "replay_blocked":
        log_audit(
            "trigger_webhook_rejected",
            {
                "source": source,
                "reason": result.get("reason") or "replay_blocked",
                "ip": client_ip,
                "fingerprint": result.get("fingerprint"),
            },
        )
        return api_response(status="error", message="replay_blocked", data=result, code=409)
    if result.get("status") == "policy_blocked":
        log_audit(
            "trigger_webhook_rejected",
            {
                "source": source,
                "reason": result.get("reason") or "trigger_policy_blocked",
                "ip": client_ip,
                "fingerprint": result.get("fingerprint"),
                "policy_precheck": result.get("policy_precheck"),
            },
        )
        return api_response(status="error", message="trigger_policy_blocked", data=result, code=422)

    return api_response(data=result)


@triggers_bp.route("/triggers/test", methods=["POST"])
@check_auth
@validate_request(TriggerTestRequest)
def test_trigger():
    """
    Testet einen Trigger ohne tatsächliche Task-Erstellung.

    Request Body:
    {
        "source": "generic",
        "payload": {"title": "Test Task", "description": "..."}
    }
    """
    data = g.validated_data
    source = str(data.source or "generic")
    payload = data.payload or {}

    try:
        tasks = get_core_services().trigger_runtime_service.preview_tasks(source=source, payload=payload)
    except Exception as e:
        return api_response(status="error", message=str(e), code=400)

    return api_response(data={"source": source, "parsed_tasks": tasks, "would_create": len(tasks)})


__all__ = ["trigger_engine", "triggers_bp", "init_triggers", "TriggerEngine"]
