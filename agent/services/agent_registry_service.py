from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from flask import current_app

from agent.db_models import AgentInfoDB
from agent.routes.tasks.orchestration_policy import normalize_capabilities, normalize_worker_roles


class AgentRegistryService:
    """Hub-owned worker registration and liveness normalization."""

    def validate_registration_payload(self, data: dict, *, registration_token: str | None) -> tuple[dict | None, str | None, int]:
        if registration_token:
            provided_token = data.get("registration_token")
            if provided_token != registration_token:
                logging.warning("Abgelehnte Registrierung fuer %s: Ungueltiger Registrierungs-Token", data.get("name"))
                return None, "Invalid or missing registration token", 401

        url = data.get("url")
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None, "invalid_agent_url", 400

        role = str(data.get("role") or "worker").strip().lower()
        if role not in {"worker", "hub"}:
            return None, "invalid_agent_role", 400

        worker_roles = normalize_worker_roles(data.get("worker_roles") or [])
        capabilities = normalize_capabilities(data.get("capabilities") or [])
        if role == "worker" and not (worker_roles or capabilities):
            return None, "worker_capabilities_required", 400

        raw_limits = dict(data.get("execution_limits") or {})
        execution_limits = {
            "max_parallel_tasks": max(1, min(int(raw_limits.get("max_parallel_tasks") or 1), 32)),
            "max_runtime_seconds": max(30, min(int(raw_limits.get("max_runtime_seconds") or 900), 86400)),
            "max_workspace_mb": max(64, min(int(raw_limits.get("max_workspace_mb") or 1024), 65536)),
        }
        normalized = {
            **data,
            "url": url,
            "role": role,
            "worker_roles": worker_roles,
            "capabilities": capabilities,
            "execution_limits": execution_limits,
        }
        return normalized, None, 200

    def validate_agent_endpoint(self, *, url: str, http_client, timeout: float) -> tuple[bool, str | None]:
        try:
            check_url = f"{url.rstrip('/')}/health"
            response = http_client.get(check_url, timeout=timeout, return_response=True, silent=True)
            if not response or response.status_code >= 500:
                response = http_client.get(url, timeout=timeout, return_response=True, silent=True)
            if not response:
                return False, f"Agent URL {url} is unreachable"
        except Exception as exc:
            return False, f"Validation failed: {str(exc)}"
        return True, None

    def build_registered_agent(self, data: dict) -> AgentInfoDB:
        return AgentInfoDB(
            url=data.get("url"),
            name=data.get("name"),
            role=data.get("role"),
            token=data.get("token"),
            worker_roles=list(data.get("worker_roles") or []),
            capabilities=list(data.get("capabilities") or []),
            execution_limits=dict(data.get("execution_limits") or {}),
            registration_validated=True,
            validation_errors=[],
            validated_at=time.time(),
            last_seen=time.time(),
            status="online",
        )

    def mark_stale_agents_offline(self, *, agents: list, timeout: float, now: float | None = None) -> list:
        current = float(now or time.time())
        updated = []
        for agent in agents:
            if agent.status == "online" and (current - agent.last_seen > timeout):
                agent.status = "offline"
                updated.append(agent)
        return updated

    def build_contract_metadata(self) -> dict:
        return {
            "registration_mode": "hub_owned_worker_directory",
            "liveness_source": "agent_registry_and_health_checks",
            "offline_timeout_seconds": int(getattr(current_app.config, "get", lambda *_: None)("AGENT_OFFLINE_TIMEOUT", 0) or 0),
        }


agent_registry_service = AgentRegistryService()


def get_agent_registry_service() -> AgentRegistryService:
    return agent_registry_service
