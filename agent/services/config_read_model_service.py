from __future__ import annotations

import time

from agent.services.repository_registry import get_repository_registry


class ConfigReadModelService:
    """Read-model builders for assistant and dashboard configuration views."""

    def assistant_read_model(
        self,
        *,
        cfg: dict,
        is_admin: bool,
        capability_contract_builder,
        allowed_tools_resolver,
        capabilities_describer,
        settings_inventory_builder,
        settings_summary_builder,
        automation_snapshot_builder,
    ) -> dict:
        repos = get_repository_registry()
        teams = [team.model_dump() for team in repos.team_repo.get_all()]
        roles = [role.model_dump() for role in repos.role_repo.get_all()]
        templates = [template.model_dump() for template in repos.template_repo.get_all()]
        agents = [agent.model_dump() for agent in repos.agent_repo.get_all()]
        for agent in agents:
            if "token" in agent:
                agent["token"] = "***"

        capability_contract = capability_contract_builder(cfg)
        allowed_tools = allowed_tools_resolver(cfg, is_admin=is_admin, contract=capability_contract)
        capability_meta = capabilities_describer(capability_contract, allowed_tools=allowed_tools, is_admin=is_admin)
        settings_inventory = settings_inventory_builder()
        settings_summary = settings_summary_builder(cfg, teams, templates)
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "settings": {
                "summary": settings_summary,
                "editable_inventory": settings_inventory,
                "editable_count": len(settings_inventory),
            },
            "automation": automation_snapshot_builder(),
            "assistant_capabilities": capability_meta,
            "context_timestamp": int(time.time()),
        }

    def dashboard_read_model(
        self,
        *,
        cfg: dict,
        benchmark_task_kind: str,
        benchmark_task_kinds: set[str] | list[str],
        benchmark_rows_builder,
        system_health_builder,
    ) -> dict:
        repos = get_repository_registry()
        teams = [team.model_dump() for team in repos.team_repo.get_all()]
        roles = [role.model_dump() for role in repos.role_repo.get_all()]
        templates = [template.model_dump() for template in repos.template_repo.get_all()]
        agents = [agent.model_dump() for agent in repos.agent_repo.get_all()]
        tasks = [task.model_dump() for task in repos.task_repo.get_all()]
        for agent in agents:
            if "token" in agent:
                agent["token"] = "***"

        task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0, "blocked": 0}
        for task in tasks:
            status = str(task.get("status") or "todo").strip().lower()
            if status not in task_counts:
                task_counts[status] = 0
            task_counts[status] += 1

        recent_tasks = sorted(
            tasks,
            key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
            reverse=True,
        )[:30]
        recent_timeline = [
            {
                "task_id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "updated_at": task.get("updated_at") or task.get("created_at"),
            }
            for task in recent_tasks
        ]
        bench_rows, bench = benchmark_rows_builder(task_kind=benchmark_task_kind, top_n=8)
        valid_task_kind = benchmark_task_kind if benchmark_task_kind in benchmark_task_kinds else "analysis"
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "system_health": system_health_builder(),
            "contracts": {"version": "v1"},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "tasks": {"counts": task_counts, "recent": recent_timeline},
            "benchmarks": {
                "task_kind": valid_task_kind,
                "updated_at": bench.get("updated_at"),
                "items": bench_rows,
            },
            "context_timestamp": int(time.time()),
        }


config_read_model_service = ConfigReadModelService()


def get_config_read_model_service() -> ConfigReadModelService:
    return config_read_model_service
