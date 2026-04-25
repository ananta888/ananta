from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

from flask import Blueprint, current_app, request

from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.service_registry import get_core_services

webhooks_bp = Blueprint("webhooks", __name__)

_DEFAULT_ALLOWED_PROVIDERS = {"github", "gitlab"}
_DEFAULT_ALLOWED_EVENTS = {"pull_request", "merge_request"}
_ARCHIVED_OR_UNKNOWN_PROVIDER_MESSAGE = "provider_not_allowed"


def _normalize_str_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _webhook_config() -> dict[str, Any]:
    cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("pr_review_webhooks", {}) or {}
    return {
        "test_mode": bool(cfg.get("test_mode", False)),
        "allowed_providers": _normalize_str_set(cfg.get("allowed_providers")) or set(_DEFAULT_ALLOWED_PROVIDERS),
        "allowed_events": _normalize_str_set(cfg.get("allowed_events")) or set(_DEFAULT_ALLOWED_EVENTS),
        "allowed_repositories": {str(item).strip() for item in list(cfg.get("allowed_repositories") or []) if str(item).strip()},
        "secrets": {
            str(key).strip().lower(): str(value)
            for key, value in dict(cfg.get("secrets") or {}).items()
            if str(key).strip() and str(value or "").strip()
        },
    }


def _is_test_mode_enabled(config: dict[str, Any]) -> bool:
    return (
        bool(current_app.testing)
        or bool(config.get("test_mode"))
        or _as_bool(request.args.get("test_mode"))
        or _as_bool(request.headers.get("X-Ananta-Test-Mode"))
    )


def _canonical_event(provider: str, headers: dict[str, Any]) -> str:
    if provider == "github":
        return str(headers.get("X-GitHub-Event") or "").strip().lower()
    if provider == "gitlab":
        raw_event = str(headers.get("X-Gitlab-Event") or "").strip().lower()
        if raw_event == "merge request hook":
            return "merge_request"
        return raw_event
    return ""


def _extract_repository(provider: str, payload: dict[str, Any]) -> str:
    if provider == "github":
        return str(((payload.get("repository") or {}).get("full_name") or "")).strip()
    if provider == "gitlab":
        return str(((payload.get("project") or {}).get("path_with_namespace") or "")).strip()
    return ""


def _extract_pr_metadata(provider: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    if provider == "github":
        pull_request = payload.get("pull_request") or {}
        return (
            str(pull_request.get("number") or "").strip(),
            str(payload.get("action") or "").strip(),
            str(pull_request.get("html_url") or "").strip(),
        )

    if provider == "gitlab":
        attrs = payload.get("object_attributes") or {}
        return (
            str(attrs.get("iid") or "").strip(),
            str(attrs.get("action") or "").strip(),
            str(attrs.get("url") or "").strip(),
        )
    return "", "", ""


def _verify_signature(provider: str, payload_raw: bytes, headers: dict[str, Any], secret: str) -> bool:
    if provider == "github":
        signature = str(headers.get("X-Hub-Signature-256") or "").strip()
        if signature.startswith("sha256="):
            signature = signature[7:]
        if not signature:
            return False
        expected = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    if provider == "gitlab":
        token = str(headers.get("X-Gitlab-Token") or "").strip()
        if not token:
            return False
        return hmac.compare_digest(token, secret)

    return False


def _build_task_id(provider: str) -> str:
    return f"prr-{provider[:3]}-{uuid.uuid4().hex[:8]}"


@webhooks_bp.route("/webhooks/git/<provider>", methods=["POST"])
def git_provider_webhook(provider: str):
    normalized_provider = str(provider or "").strip().lower()
    config = _webhook_config()
    if normalized_provider not in config["allowed_providers"]:
        return api_response(status="error", message=_ARCHIVED_OR_UNKNOWN_PROVIDER_MESSAGE, code=403)

    if not _is_test_mode_enabled(config):
        return api_response(status="error", message="webhook_test_mode_required", code=403)

    payload_raw = request.get_data() or b""
    try:
        payload = json.loads(payload_raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return api_response(status="error", message="invalid_json", code=400)
    if not isinstance(payload, dict):
        return api_response(status="error", message="invalid_json", code=400)

    secret = str((config["secrets"] or {}).get(normalized_provider) or "").strip()
    if not secret:
        return api_response(status="error", message="missing_provider_secret", code=412)

    headers = dict(request.headers)
    if not _verify_signature(normalized_provider, payload_raw, headers, secret):
        return api_response(status="error", message="invalid_signature", code=401)

    event_type = _canonical_event(normalized_provider, headers)
    if event_type not in config["allowed_events"]:
        return api_response(status="error", message="unsupported_event_type", data={"event_type": event_type}, code=422)

    repository = _extract_repository(normalized_provider, payload)
    if not repository or repository not in config["allowed_repositories"]:
        return api_response(status="error", message="repository_not_allowed", data={"repository": repository}, code=403)

    pr_number, action, pr_url = _extract_pr_metadata(normalized_provider, payload)
    if not pr_number:
        return api_response(status="error", message="unsupported_event_type", data={"event_type": event_type}, code=422)

    task_id = _build_task_id(normalized_provider)
    title = f"PR review request {repository}#{pr_number}"
    description = (
        f"Provider: {normalized_provider}\n"
        f"Repository: {repository}\n"
        f"Event: {event_type}\n"
        f"Action: {action or 'unknown'}\n"
        f"URL: {pr_url or 'n/a'}\n\n"
        "Review-only mode: analyze diff, run allowed checks and produce ReviewArtifact."
    )
    get_core_services().task_queue_service.ingest_task(
        task_id=task_id,
        status="todo",
        title=title,
        description=description,
        priority="medium",
        created_by=f"webhook:{normalized_provider}",
        source="git_webhook_receiver",
        tags=["pr-review", normalized_provider, repository],
        event_type="pr_review_requested",
        event_channel="git_webhook_receiver",
        event_details={
            "provider": normalized_provider,
            "repository": repository,
            "event_type": event_type,
            "action": action,
            "pull_request_number": pr_number,
            "delivery_id": str(headers.get("X-GitHub-Delivery") or headers.get("X-Gitlab-Event-UUID") or ""),
            "review_only": True,
            "execution_mode": "queued_only",
        },
    )
    log_audit(
        "git_pr_review_webhook_queued",
        {
            "provider": normalized_provider,
            "repository": repository,
            "event_type": event_type,
            "task_id": task_id,
        },
    )
    return api_response(
        data={
            "status": "queued",
            "task_id": task_id,
            "provider": normalized_provider,
            "repository": repository,
            "event_type": event_type,
            "execution": "queued_only",
        }
    )

