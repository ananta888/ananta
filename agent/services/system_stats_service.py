from __future__ import annotations

import logging
import os
import time

import psutil

from agent.config import settings
from agent.db_models import StatsSnapshotDB
from agent.metrics import CPU_USAGE, RAM_USAGE
from agent.repository import banned_ip_repo, login_attempt_repo
from agent.services.repository_registry import get_repository_registry


class SystemStatsService:
    """Builds hub system stats read models and persists periodic stats snapshots."""

    def get_resource_usage(self) -> dict:
        try:
            process = psutil.Process(os.getpid())
            cpu = process.cpu_percent(interval=None)
            ram = process.memory_info().rss
            CPU_USAGE.set(cpu)
            RAM_USAGE.set(ram)
            return {"cpu_percent": cpu, "ram_bytes": ram}
        except Exception as exc:
            logging.error(f"Error getting resource usage: {exc}")
            return {"cpu_percent": 0, "ram_bytes": 0}

    def build_system_stats_read_model(self, *, agent_name: str | None = None) -> dict:
        repos = get_repository_registry()
        agents = repos.agent_repo.get_all()
        agent_counts = {"total": len(agents), "online": 0, "offline": 0}
        for agent in agents:
            status = agent.status or "offline"
            if status not in agent_counts:
                agent_counts[status] = 0
            agent_counts[status] += 1

        tasks = repos.task_repo.get_all()
        task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0}
        for task in tasks:
            status = task.status or "unknown"
            if status not in task_counts:
                task_counts[status] = 0
            task_counts[status] += 1

        from agent.shell import get_shell_pool

        pool = get_shell_pool()
        free_shells = pool.pool.qsize()
        shell_stats = {"total": pool.size, "free": free_shells, "busy": len(pool.shells) - free_shells}

        return {
            "agents": agent_counts,
            "tasks": task_counts,
            "shell_pool": shell_stats,
            "resources": self.get_resource_usage(),
            "timestamp": time.time(),
            "agent_name": agent_name,
        }

    def get_stats_history(self, *, limit: int | None, offset: int = 0) -> list[dict]:
        repos = get_repository_registry()
        return [snapshot.model_dump() for snapshot in repos.stats_repo.get_all(limit=limit, offset=offset)]

    def record_stats_snapshot(self, *, agent_name: str | None = None) -> None:
        try:
            model = self.build_system_stats_read_model(agent_name=agent_name)
            repos = get_repository_registry()
            repos.stats_repo.save(
                StatsSnapshotDB(
                    agents=model["agents"],
                    tasks=model["tasks"],
                    shell_pool=model["shell_pool"],
                    resources=model["resources"],
                    timestamp=model["timestamp"],
                )
            )
            repos.stats_repo.delete_old(settings.stats_history_size)
            login_attempt_repo.delete_old(max_age_seconds=86400)
            banned_ip_repo.delete_expired()
        except Exception as exc:
            is_db_err = "OperationalError" in str(exc) or "psycopg2" in str(exc)
            if is_db_err:
                logging.info("Statistik-Aufzeichnung uebersprungen: Datenbank nicht erreichbar.")
            else:
                logging.error(f"Fehler beim Aufzeichnen der Statistik-Historie: {exc}")


system_stats_service = SystemStatsService()


def get_system_stats_service() -> SystemStatsService:
    return system_stats_service
