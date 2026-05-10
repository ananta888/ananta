from __future__ import annotations

import concurrent.futures
import logging
import time

from agent.services.repository_registry import get_repository_registry
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.utils import read_json, write_json


class AgentHealthMonitorService:
    """Checks worker liveness and updates the hub-owned worker directory."""

    def check_all_agents_health(
        self,
        *,
        app,
        failure_state: dict[str, int],
        failure_lock,
        offline_failure_threshold: int,
    ) -> None:
        with app.app_context():
            try:
                repos = get_repository_registry()
                agents = repos.agent_repo.get_all()
                now = time.time()

                if not agents:
                    self._check_file_agents(app=app, now=now)
                    return

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(self._check_agent, agent) for agent in agents]
                    for future in concurrent.futures.as_completed(futures):
                        agent_obj, result = future.result()
                        if not result:
                            continue
                        new_status, _resources = result
                        agent_key = (agent_obj.url or agent_obj.name or "").strip() or agent_obj.name
                        effective_status = new_status

                        with failure_lock:
                            if new_status == "online":
                                failure_state[agent_key] = 0
                            else:
                                failures = int(failure_state.get(agent_key, 0)) + 1
                                failure_state[agent_key] = failures
                                if failures < offline_failure_threshold and agent_obj.status == "online":
                                    effective_status = "online"

                        changed = False
                        if agent_obj.status != effective_status:
                            logging.info(f"Agent {agent_obj.name} Statusaenderung: {agent_obj.status} -> {effective_status}")
                            agent_obj.status = effective_status
                            changed = True
                        if effective_status == "online":
                            agent_obj.last_seen = now
                            changed = True
                        if changed:
                            repos.agent_repo.save(agent_obj)
                reconciliation = get_task_execution_tracking_service().reconcile_worker_executions(now=now)
                if reconciliation.get("decisions"):
                    logging.warning(
                        "Worker execution reconciliation applied for %s task(s)",
                        len(reconciliation.get("decisions") or []),
                    )
            except Exception as exc:
                is_db_err = "OperationalError" in str(exc) or "psycopg2" in str(exc)
                if is_db_err:
                    logging.info("Agent-Health-Check uebersprungen: Datenbank nicht erreichbar.")
                else:
                    logging.error(f"Fehler beim Agent-Health-Check: {exc}")

    def _check_file_agents(self, *, app, now: float) -> None:
        agents_path = app.config.get("AGENTS_PATH", "data/agents.json")
        file_agents = read_json(agents_path, {}) or {}
        changed = False
        for _name, info in file_agents.items():
            previous = info.get("status")
            self._check_name(info=info, now=now)
            if info.get("status") != previous:
                changed = True
        if changed:
            write_json(agents_path, file_agents)

    @staticmethod
    def _health_check_url(url: str, token: str | None, timeout: float = 10.0) -> tuple[bool, dict | None]:
        """Check agent liveness via /health?basic=1 (primary), /health (fallback).

        Returns (is_online, resource_data_or_None).  The /health endpoint is
        intentionally lightweight so it returns quickly even when the worker
        is busy with an LLM call.
        """
        from agent.common.http import get_default_client

        http = get_default_client()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        base = url.rstrip("/")

        # Primary: lightweight health check
        for path, health_timeout in (("/health?basic=1", timeout), ("/health", timeout * 2)):
            try:
                resp = http.get(f"{base}{path}", headers=headers, timeout=health_timeout, return_response=True, silent=True)
                if resp and resp.status_code < 500:
                    return True, None
            except Exception:
                continue

        return False, None

    def _check_name(self, *, info: dict, now: float) -> bool | None:
        url = info.get("url")
        token = info.get("token")
        if not url:
            return None
        is_online, _ = self._health_check_url(url, token, timeout=10.0)
        info["status"] = "online" if is_online else "offline"
        if is_online:
            info["last_seen"] = now
        return is_online

    def _check_agent(self, agent_obj):
        url = agent_obj.url
        token = agent_obj.token
        if not url:
            return agent_obj, None
        is_online, _ = self._health_check_url(url, token, timeout=10.0)
        if not is_online:
            return agent_obj, ("offline", None)

        resources = None
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            from agent.common.http import get_default_client

            http_client = get_default_client()
            stats_resp = http_client.get(
                f"{url.rstrip('/')}/stats", headers=headers,
                timeout=5.0, return_response=True, silent=True,
            )
            if stats_resp and stats_resp.status_code == 200:
                try:
                    resources = stats_resp.json().get("resources")
                except Exception:
                    pass
        except Exception:
            pass

        return agent_obj, ("online", resources)


agent_health_monitor_service = AgentHealthMonitorService()


def get_agent_health_monitor_service() -> AgentHealthMonitorService:
    return agent_health_monitor_service
