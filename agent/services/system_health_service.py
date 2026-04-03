from __future__ import annotations

import concurrent.futures
import time
from typing import Any

from flask import Flask

from agent.common.http import get_default_client
from agent.config import settings
from agent.llm_integration import probe_lmstudio_runtime
from agent.repository import agent_repo, task_repo
from agent.runtime_profiles import resolve_runtime_profile
from agent.services.background.registration import get_registration_state
from agent.services.scheduler_service import get_scheduler_service
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.shell import get_shell

http_client = get_default_client()


def _runtime_default_provider(app: Flask) -> str:
    cfg = app.config.get("AGENT_CONFIG", {}) or {}
    return str(cfg.get("default_provider") or settings.default_provider or "").strip().lower()


def _runtime_provider_urls(app: Flask) -> dict[str, str]:
    return app.config.get("PROVIDER_URLS", {}) or {}


def _component_status(statuses: list[str]) -> str:
    normalized = [str(item or "").strip().lower() for item in statuses if str(item or "").strip()]
    if not normalized:
        return "unknown"
    if any(item == "error" for item in normalized):
        return "error"
    if any(item in {"unstable", "degraded"} for item in normalized):
        return "degraded"
    if all(item == "ok" for item in normalized):
        return "ok"
    return "unknown"


def build_system_health_payload(app: Flask, *, basic_mode: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    agent_name = app.config.get("AGENT_NAME")
    role = str(settings.role or "worker").strip().lower()
    started_at = float(app.config.get("APP_STARTED_AT") or time.time())

    try:
        shell = get_shell()
        checks["shell"] = {"status": "ok" if shell.is_healthy() else "down"}
    except Exception as exc:
        checks["shell"] = {"status": "error", "message": str(exc)}

    if basic_mode:
        return {
            "status": _component_status([checks["shell"].get("status")]),
            "agent": agent_name,
            "role": role,
            "uptime_seconds": max(0, int(time.time() - started_at)),
            "checks": checks,
        }

    llm_checks: dict[str, str] = {}
    provider_urls = _runtime_provider_urls(app)
    active_providers = {_runtime_default_provider(app)}
    if app.config.get("OPENAI_API_KEY") or settings.openai_api_key:
        active_providers.add("openai")
    if app.config.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key:
        active_providers.add("anthropic")
    if provider_urls.get("ollama") and provider_urls.get("ollama") != "http://localhost:11434/api/generate":
        active_providers.add("ollama")
    if provider_urls.get("lmstudio") and provider_urls.get("lmstudio") != "http://192.168.56.1:1234/v1/completions":
        active_providers.add("lmstudio")

    def _check_provider(provider: str) -> tuple[str, str | None]:
        url = provider_urls.get(provider)
        if not url:
            return provider, None
        if provider == "lmstudio":
            probe = probe_lmstudio_runtime(url, timeout=min(settings.http_timeout, 3.0))
            if probe["ok"]:
                return provider, ("ok" if probe["candidate_count"] > 0 else "unstable")
            return provider, "unreachable" if probe["status"] != "invalid_url" else "error"
        try:
            res = http_client.get(url, timeout=min(settings.http_timeout, 3.0), return_response=True, silent=True)
            if res:
                return provider, ("ok" if res.status_code < 500 else "unstable")
            return provider, "unreachable"
        except Exception:
            return provider, "error"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active_providers))) as executor:
        futures = [executor.submit(_check_provider, provider) for provider in active_providers]
        for future in concurrent.futures.as_completed(futures):
            provider, status = future.result()
            if status:
                llm_checks[provider] = status

    if llm_checks:
        checks["llm_providers"] = llm_checks

    queue_counts = {"todo": 0, "assigned": 0, "in_progress": 0, "blocked": 0, "completed": 0, "failed": 0}
    agents = agent_repo.get_all()
    tasks = task_repo.get_all()
    for task in tasks:
        status = str(task.status or "").strip().lower()
        if status in queue_counts:
            queue_counts[status] += 1

    checks["queue"] = {
        "status": "ok",
        "depth": queue_counts["todo"] + queue_counts["assigned"] + queue_counts["blocked"],
        "counts": queue_counts,
    }
    try:
        checks["scheduler"] = {"status": "ok", **get_scheduler_service().runtime_state()}
    except Exception as exc:
        checks["scheduler"] = {"status": "error", "message": str(exc)}
    checks["agents"] = {
        "status": "ok",
        "total": len(agents),
        "online": len([item for item in agents if str(item.status or "").strip().lower() == "online"]),
        "offline": len([item for item in agents if str(item.status or "").strip().lower() == "offline"]),
    }
    checks["worker_execution_reconciliation"] = get_task_execution_tracking_service().build_execution_reconciliation_snapshot()
    runtime_profile = resolve_runtime_profile(app.config.get("AGENT_CONFIG", {}) or {})
    checks["runtime_profile"] = {
        "status": "ok" if runtime_profile.get("valid") else "error",
        "requested": runtime_profile.get("requested"),
        "effective": runtime_profile.get("effective"),
        "source": runtime_profile.get("source"),
        "validation": runtime_profile.get("validation"),
    }

    try:
        registration = get_registration_state()
        registration_status = "disabled"
        if registration.get("enabled"):
            registration_status = "ok" if registration.get("last_success_at") else (
                "degraded" if registration.get("running") or registration.get("attempts") else "error"
            )
        checks["registration"] = {"status": registration_status, **registration}
    except Exception as exc:
        checks["registration"] = {"status": "error", "message": str(exc)}

    top_level_status = _component_status(
        [
            checks.get("shell", {}).get("status"),
            *(llm_checks.values() if llm_checks else []),
            checks["registration"].get("status"),
            checks["worker_execution_reconciliation"].get("status"),
            checks["runtime_profile"].get("status"),
        ]
    )
    if top_level_status == "unknown":
        top_level_status = "ok"

    return {
        "status": top_level_status,
        "agent": agent_name,
        "role": role,
        "uptime_seconds": max(0, int(time.time() - started_at)),
        "checks": checks,
    }
