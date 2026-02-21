"""
Trigger-System: Webhooks und externe Event-Quellen fuer automatische Task-Erstellung.

Unterstützte Trigger:
- Webhooks (allgemein)
- GitHub Issues/PRs
- Custom JSON-Payloads
"""

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from typing import Callable, Optional

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import ConfigDB
from agent.repository import config_repo
from agent.routes.tasks.utils import _update_local_task_status

triggers_bp = Blueprint("triggers", __name__)

TRIGGERS_CONFIG_KEY = "triggers_config"

DEFAULT_RATE_LIMIT = 60
DEFAULT_RATE_WINDOW = 60


def _generate_trigger_task_id(source: str) -> str:
    short = uuid.uuid4().hex[:8]
    return f"trg-{source[:4]}-{short}"


class TriggerEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._handlers: dict[str, Callable] = {}
        self._webhook_secrets: dict[str, str] = {}
        self._enabled_sources: set[str] = set()
        self._ip_whitelist: dict[str, set[str]] = defaultdict(set)
        self._rate_limits: dict[str, tuple[int, int]] = {}
        self._rate_counters: dict[str, list[float]] = defaultdict(list)
        self._stats = {
            "webhooks_received": 0,
            "tasks_created": 0,
            "rejected": 0,
            "rate_limited": 0,
            "ip_blocked": 0,
        }
        self.auto_start_planner = True

    def register_handler(self, source: str, handler: Callable):
        with self._lock:
            self._handlers[source] = handler
            logging.info(f"Trigger handler registered: {source}")

    def unregister_handler(self, source: str):
        with self._lock:
            self._handlers.pop(source, None)

    def set_webhook_secret(self, source: str, secret: str):
        with self._lock:
            if secret:
                self._webhook_secrets[source] = secret
            else:
                self._webhook_secrets.pop(source, None)

    def enable_source(self, source: str, enabled: bool = True):
        with self._lock:
            if enabled:
                self._enabled_sources.add(source)
            else:
                self._enabled_sources.discard(source)

    def is_source_enabled(self, source: str) -> bool:
        return source in self._enabled_sources

    def set_ip_whitelist(self, source: str, ips: list[str]):
        with self._lock:
            self._ip_whitelist[source] = set(ip.strip() for ip in ips if ip.strip())

    def get_ip_whitelist(self, source: str) -> list[str]:
        return list(self._ip_whitelist.get(source, set()))

    def is_ip_allowed(self, source: str, ip: str) -> bool:
        whitelist = self._ip_whitelist.get(source, set())
        if not whitelist:
            return True
        return ip in whitelist

    def set_rate_limit(self, source: str, max_requests: int, window_seconds: int):
        with self._lock:
            self._rate_limits[source] = (max_requests, window_seconds)

    def get_rate_limit(self, source: str) -> tuple[int, int]:
        return self._rate_limits.get(source, (DEFAULT_RATE_LIMIT, DEFAULT_RATE_WINDOW))

    def check_rate_limit(self, source: str, ip: str) -> bool:
        max_requests, window = self.get_rate_limit(source)
        key = f"{source}:{ip}"
        now = time.time()

        with self._lock:
            self._rate_counters[key] = [ts for ts in self._rate_counters[key] if now - ts < window]
            if len(self._rate_counters[key]) >= max_requests:
                return False
            self._rate_counters[key].append(now)
            return True

    def verify_webhook_signature(self, source: str, payload: bytes, signature: str) -> bool:
        secret = self._webhook_secrets.get(source)
        if not secret:
            return True

        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        if signature.startswith("sha256="):
            signature = signature[7:]

        return hmac.compare_digest(expected, signature)

    def process_webhook(
        self,
        source: str,
        payload: dict,
        headers: Optional[dict] = None,
        client_ip: Optional[str] = None,
    ) -> dict:
        self._stats["webhooks_received"] += 1

        if not self.is_source_enabled(source):
            self._stats["rejected"] += 1
            return {"status": "disabled", "source": source}

        if client_ip and not self.is_ip_allowed(source, client_ip):
            self._stats["ip_blocked"] += 1
            logging.warning(f"Webhook from blocked IP: {client_ip} for source {source}")
            return {"status": "ip_blocked", "source": source}

        if client_ip and not self.check_rate_limit(source, client_ip):
            self._stats["rate_limited"] += 1
            return {"status": "rate_limited", "source": source}

        handler = self._handlers.get(source)
        if handler:
            try:
                tasks = handler(payload, headers or {})
            except Exception as e:
                logging.error(f"Trigger handler error for {source}: {e}")
                return {"status": "error", "error": str(e)}
        else:
            tasks = self._default_handler(source, payload)

        created_ids = []
        for task_data in tasks:
            task_id = task_data.get("id") or _generate_trigger_task_id(source)
            title = str(task_data.get("title") or "")[:200]
            description = str(task_data.get("description") or "")[:2000]
            priority = str(task_data.get("priority") or "Medium")
            team_id = task_data.get("team_id")
            tags = task_data.get("tags", [])

            _update_local_task_status(
                task_id,
                "todo",
                title=title or f"Trigger: {source}",
                description=description,
                priority=priority,
                team_id=team_id,
                event_type="trigger_created",
                event_actor=f"trigger:{source}",
                event_details={"source": source, "tags": tags, "payload_preview": str(payload)[:200]},
            )
            created_ids.append(task_id)
            self._stats["tasks_created"] += 1

        log_audit(
            "trigger_webhook_processed",
            {
                "source": source,
                "tasks_created": len(created_ids),
                "task_ids": created_ids[:10],
            },
        )

        if self.auto_start_planner and created_ids:
            self._ensure_autopilot_running()

        return {"status": "processed", "tasks_created": len(created_ids), "task_ids": created_ids}

    def _default_handler(self, source: str, payload: dict) -> list[dict]:
        tasks = []

        if "tasks" in payload and isinstance(payload["tasks"], list):
            for t in payload["tasks"]:
                if isinstance(t, dict) and (t.get("description") or t.get("title")):
                    tasks.append(t)
        elif "description" in payload or "title" in payload:
            tasks.append(payload)
        elif "issue" in payload:
            tasks.extend(self._handle_github_issue(payload))
        elif "pull_request" in payload:
            tasks.extend(self._handle_github_pr(payload))

        return tasks

    def _handle_github_issue(self, payload: dict) -> list[dict]:
        issue = payload.get("issue", {})
        action = payload.get("action", "")
        repo = payload.get("repository", {}).get("full_name", "unknown")

        if action not in ("opened", "reopened", "labeled"):
            return []

        title = f"GitHub Issue: {issue.get('title', 'N/A')}"
        description = f"""Repository: {repo}
Issue #{issue.get("number")}: {issue.get("title")}
URL: {issue.get("html_url")}

{issue.get("body", "")}"""

        return [
            {
                "title": title,
                "description": description,
                "priority": "High"
                if any(l.get("name", "").lower() in ("bug", "critical") for l in issue.get("labels", []))
                else "Medium",
                "tags": ["github", "issue", repo],
            }
        ]

    def _handle_github_pr(self, payload: dict) -> list[dict]:
        pr = payload.get("pull_request", {})
        action = payload.get("action", "")
        repo = payload.get("repository", {}).get("full_name", "unknown")

        if action not in ("opened", "synchronize", "ready_for_review"):
            return []

        title = f"GitHub PR Review: {pr.get('title', 'N/A')}"
        description = f"""Repository: {repo}
PR #{pr.get("number")}: {pr.get("title")}
URL: {pr.get("html_url")}

{pr.get("body", "")}"""

        return [
            {
                "title": title,
                "description": description,
                "priority": "Medium",
                "tags": ["github", "pr", repo],
            }
        ]

    def _handle_slack_event(self, payload: dict) -> list[dict]:
        event_type = payload.get("type", "")
        event = payload.get("event", {})

        if event_type == "url_verification":
            return []

        if event_type != "event_callback":
            return []

        text = event.get("text", "")
        user = event.get("user", "")
        channel = event.get("channel", "")

        if not text:
            return []

        title = f"Slack: {text[:80]}..."
        description = f"""From: @{user}
Channel: #{channel}
Message: {text}
"""
        return [
            {
                "title": title,
                "description": description,
                "priority": "Medium",
                "tags": ["slack", channel],
            }
        ]

    def _handle_jira_event(self, payload: dict) -> list[dict]:
        issue = payload.get("issue", {})
        webhook_event = payload.get("webhookEvent", "")

        if not issue:
            return []

        key = issue.get("key", "")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        description = fields.get("description", "")
        priority_name = (fields.get("priority", {}) or {}).get("name", "Medium")
        issue_type = (fields.get("issuetype", {}) or {}).get("name", "")

        if webhook_event in ("jira:issue_deleted",):
            return []

        title = f"Jira {issue_type}: {summary}"
        desc = f"""Issue: {key}
Type: {issue_type}
Priority: {priority_name}

{description or "No description"}
"""
        mapped_priority = "High" if priority_name.lower() in ("highest", "high", "critical") else "Medium"

        return [
            {
                "title": title,
                "description": desc,
                "priority": mapped_priority,
                "tags": ["jira", issue_type.lower(), key],
            }
        ]

    def _handle_email_event(self, payload: dict) -> list[dict]:
        from_email = payload.get("from", "")
        subject = payload.get("subject", "")
        body = payload.get("body", "") or payload.get("text", "")
        html_body = payload.get("html", "")

        if not subject:
            return []

        content = body or html_body
        content_preview = content[:500] if content else ""

        title = f"Email: {subject[:80]}"
        description = f"""From: {from_email}
Subject: {subject}

{content_preview}
"""
        priority = "Medium"
        subject_lower = subject.lower()
        if any(kw in subject_lower for kw in ["urgent", "critical", "asap", "emergency"]):
            priority = "High"
        elif any(kw in subject_lower for kw in ["fyi", "info", "newsletter"]):
            priority = "Low"

        return [
            {
                "title": title,
                "description": description,
                "priority": priority,
                "tags": ["email", from_email],
            }
        ]

    def _ensure_autopilot_running(self):
        try:
            from agent.routes.tasks.autopilot import autonomous_loop

            if not autonomous_loop.running:
                from agent.repository import team_repo

                active_team = next((t for t in team_repo.get_all() if t.is_active), None)
                autonomous_loop.start(
                    interval_seconds=20,
                    max_concurrency=2,
                    team_id=active_team.id if active_team else None,
                    security_level="safe",
                    persist=True,
                )
                logging.info("Trigger engine started autopilot automatically")
        except Exception as e:
            logging.warning(f"Could not start autopilot: {e}")

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled_sources": list(self._enabled_sources),
                "configured_handlers": list(self._handlers.keys()),
                "webhook_secrets_configured": list(self._webhook_secrets.keys()),
                "ip_whitelists": {k: list(v) for k, v in self._ip_whitelist.items()},
                "rate_limits": {
                    k: {"max_requests": v[0], "window_seconds": v[1]} for k, v in self._rate_limits.items()
                },
                "stats": dict(self._stats),
                "auto_start_planner": self.auto_start_planner,
            }

    def configure(
        self,
        enabled_sources: Optional[list[str]] = None,
        webhook_secrets: Optional[dict[str, str]] = None,
        auto_start_planner: Optional[bool] = None,
        ip_whitelists: Optional[dict[str, list[str]]] = None,
        rate_limits: Optional[dict[str, dict]] = None,
    ) -> dict:
        with self._lock:
            if enabled_sources is not None:
                self._enabled_sources = set(enabled_sources)
            if webhook_secrets is not None:
                self._webhook_secrets = {k: v for k, v in webhook_secrets.items() if v}
            if auto_start_planner is not None:
                self.auto_start_planner = bool(auto_start_planner)
            if ip_whitelists is not None:
                for source, ips in ip_whitelists.items():
                    self._ip_whitelist[source] = set(ips)
            if rate_limits is not None:
                for source, cfg in rate_limits.items():
                    if isinstance(cfg, dict):
                        self._rate_limits[source] = (
                            cfg.get("max_requests", DEFAULT_RATE_LIMIT),
                            cfg.get("window_seconds", DEFAULT_RATE_WINDOW),
                        )
        return self.status()


trigger_engine = TriggerEngine()


def init_triggers():
    try:
        cfg = config_repo.get_by_key(TRIGGERS_CONFIG_KEY)
        if cfg:
            data = json.loads(cfg.value_json or "{}")
            trigger_engine.configure(
                enabled_sources=data.get("enabled_sources"),
                webhook_secrets=data.get("webhook_secrets"),
                auto_start_planner=data.get("auto_start_planner"),
                ip_whitelists=data.get("ip_whitelists"),
                rate_limits=data.get("rate_limits"),
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
    return api_response(data=trigger_engine.status())


@triggers_bp.route("/triggers/configure", methods=["POST"])
@check_auth
@admin_required
def triggers_configure():
    data = request.get_json(silent=True) or {}
    new_config = trigger_engine.configure(
        enabled_sources=data.get("enabled_sources"),
        webhook_secrets=data.get("webhook_secrets"),
        auto_start_planner=data.get("auto_start_planner"),
        ip_whitelists=data.get("ip_whitelists"),
        rate_limits=data.get("rate_limits"),
    )
    config_repo.save(ConfigDB(key=TRIGGERS_CONFIG_KEY, value_json=json.dumps(new_config)))
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

    if not trigger_engine.verify_webhook_signature(source, payload_raw, signature):
        log_audit("trigger_webhook_rejected", {"source": source, "reason": "invalid_signature", "ip": client_ip})
        return api_response(status="error", message="invalid_signature", code=401)

    result = trigger_engine.process_webhook(source, payload, dict(request.headers), client_ip=client_ip)

    if result.get("status") == "disabled":
        return api_response(status="error", message="source_disabled", code=403)
    if result.get("status") == "ip_blocked":
        return api_response(status="error", message="ip_not_whitelisted", code=403)
    if result.get("status") == "rate_limited":
        return api_response(status="error", message="rate_limit_exceeded", code=429)

    return api_response(data=result)


@triggers_bp.route("/triggers/test", methods=["POST"])
@check_auth
def test_trigger():
    """
    Testet einen Trigger ohne tatsächliche Task-Erstellung.

    Request Body:
    {
        "source": "generic",
        "payload": {"title": "Test Task", "description": "..."}
    }
    """
    data = request.get_json(silent=True) or {}
    source = str(data.get("source") or "generic")
    payload = data.get("payload", {})

    handler = trigger_engine._handlers.get(source)
    if handler:
        try:
            tasks = handler(payload, {})
        except Exception as e:
            return api_response(status="error", message=str(e), code=400)
    else:
        tasks = trigger_engine._default_handler(source, payload)

    return api_response(data={"source": source, "parsed_tasks": tasks, "would_create": len(tasks)})
