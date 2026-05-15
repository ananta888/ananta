from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from flask import current_app

from agent.db_models import AgentInfoDB
from agent.models import AgentDirectoryEntryContract, AgentLivenessContract, WorkerExecutionLimitsContract
from agent.repository import agent_repo
from agent.routes.tasks.orchestration_policy import normalize_capabilities, normalize_worker_roles
from worker.core.runtime_target import WorkerKind


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
        strategy_mode = str(data.get("strategy_mode") or raw_limits.get("strategy_mode") or "").strip().lower() or None
        if strategy_mode:
            execution_limits["strategy_mode"] = strategy_mode
        runtime_targets = list(data.get("runtime_targets") or [])
        # Simple validation: must be list of dicts
        if not isinstance(runtime_targets, list):
             return None, "invalid_runtime_targets_format", 400

        normalized = {
            **data,
            "url": url,
            "role": role,
            "worker_roles": worker_roles,
            "capabilities": capabilities,
            "runtime_targets": runtime_targets,
            "execution_limits": execution_limits,
            "worker_kind": str(data.get("worker_kind") or "").strip().lower() or None,
            "strategy_mode": strategy_mode,
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
        execution_limits = dict(data.get("execution_limits") or {})
        worker_kind = str(data.get("worker_kind") or "").strip().lower() or None
        if worker_kind:
            execution_limits["worker_kind"] = worker_kind
        return AgentInfoDB(
            url=data.get("url"),
            name=data.get("name"),
            role=data.get("role"),
            token=data.get("token"),
            worker_roles=list(data.get("worker_roles") or []),
            capabilities=list(data.get("capabilities") or []),
            runtime_targets=list(data.get("runtime_targets") or []),
            execution_limits=execution_limits,
            registration_validated=True,
            validation_errors=[],
            validated_at=time.time(),
            last_seen=time.time(),
            status="online",
        )

    def agent_to_candidate(self, agent: AgentInfoDB) -> WorkerCandidate:
        """Map AgentInfoDB to WorkerCandidate for selection logic. DRR-T050."""
        from worker.core.runtime_target import RuntimeHealthState, WorkerCandidate, WorkerKind

        # Prefer explicit worker_kind from registration; fallback to legacy name heuristics.
        kind = WorkerKind.native_ananta_worker
        explicit_kind = str((agent.execution_limits or {}).get("worker_kind") or "").strip().lower()
        if explicit_kind in {item.value for item in WorkerKind}:
            kind = WorkerKind(explicit_kind)
        else:
            name_lower = (agent.name or "").lower()
            if "opencode" in name_lower:
                kind = WorkerKind.opencode
            elif "hermes" in name_lower:
                kind = WorkerKind.hermes
            elif "shellgpt" in name_lower:
                kind = WorkerKind.shellgpt

        health = (
            RuntimeHealthState.ready
            if agent.status == "online"
            else RuntimeHealthState.unavailable
        )

        return WorkerCandidate(
            worker_id=agent.url,
            worker_kind=kind,
            display_name=agent.name,
            capabilities=list(agent.capabilities or []),
            roles=list(agent.worker_roles or []),
            max_parallel_tasks=max(1, int((agent.execution_limits or {}).get("max_parallel_tasks") or 1)),
            runtime_target_ids=[
                rt.get("runtime_target_id")
                for rt in (agent.runtime_targets or [])
                if rt.get("runtime_target_id")
            ],
            health_state=health,
            validation_errors=list(agent.validation_errors or []),
            priority=100,
        )

    def build_directory_entry(self, *, agent: AgentInfoDB, timeout: float, now: float | None = None) -> dict:
        current = float(now or time.time())
        execution_limits = dict(agent.execution_limits or {})
        strategy_mode = str(execution_limits.get("strategy_mode") or "").strip().lower() or None
        reported_load = max(0, int(execution_limits.get("current_load") or 0))
        scheduler_load = max(0, int(execution_limits.get("scheduler_load") or 0))
        current_load = max(reported_load, scheduler_load)
        max_parallel = max(1, int(execution_limits.get("max_parallel_tasks") or 1))
        stale_seconds = max(0, int(current - float(agent.last_seen or 0)))
        available_for_routing = (
            str(agent.status or "").lower() == "online"
            and bool(agent.registration_validated)
            and current_load < max_parallel
        )
        return AgentDirectoryEntryContract(
            name=agent.name,
            url=agent.url,
            role=agent.role,
            worker_roles=list(agent.worker_roles or []),
            capabilities=list(agent.capabilities or []),
            runtime_targets=list(agent.runtime_targets or []),
            execution_limits=WorkerExecutionLimitsContract(
                max_parallel_tasks=max_parallel,
                max_runtime_seconds=max(30, int(execution_limits.get("max_runtime_seconds") or 900)),
                max_workspace_mb=max(64, int(execution_limits.get("max_workspace_mb") or 1024)),
            ),
            status=agent.status,
            registration_validated=bool(agent.registration_validated),
            validation_errors=list(agent.validation_errors or []),
            current_load=current_load,
            reported_load=reported_load,
            scheduler_load=scheduler_load,
            available_for_routing=available_for_routing,
            routing_signals=dict(execution_limits.get("routing_signals") or {}),
            security_level=str(execution_limits.get("security_level") or "medium"),
            strategy_mode=strategy_mode,
            liveness=AgentLivenessContract(
                status=str(agent.status or "offline"),
                last_seen=float(agent.last_seen or 0),
                stale_seconds=stale_seconds,
                offline_timeout_seconds=max(0, int(timeout or 0)),
                available_for_routing=available_for_routing,
            ),
        ).model_dump()

    def mark_stale_agents_offline(self, *, agents: list, timeout: float, now: float | None = None) -> list:
        current = float(now or time.time())
        updated = []
        for agent in agents:
            if agent.status == "online" and (current - agent.last_seen > timeout):
                agent.status = "offline"
                updated.append(agent)
        return updated

    def get_online_agents(self) -> list:
        """Fetch all online agents and return raw AgentInfoDB objects."""
        agents = agent_repo.get_all()
        return [a for a in agents if a.status == "online"]

    def get_online_candidates(self) -> list:
        """Fetch all online agents and return them as WorkerCandidate list."""
        from worker.core.runtime_target import WorkerCandidate

        agents = agent_repo.get_all()
        candidates: list[WorkerCandidate] = []
        for a in agents:
            if a.status != "online":
                continue
            candidates.append(self.agent_to_candidate(a))
        return candidates

    def build_contract_metadata(self) -> dict:
        return {
            "registration_mode": "hub_owned_worker_directory",
            "liveness_source": "agent_registry_and_health_checks",
            "offline_timeout_seconds": int(getattr(current_app.config, "get", lambda *_: None)("AGENT_OFFLINE_TIMEOUT", 0) or 0),
        }


agent_registry_service = AgentRegistryService()


def get_agent_registry_service() -> AgentRegistryService:
    return agent_registry_service
