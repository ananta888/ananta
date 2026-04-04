from __future__ import annotations

import time

from agent.common.sgpt import resolve_codex_runtime_config
from agent.research_backend import get_research_backend_preflight, resolve_research_backend_config
from agent.runtime_profiles import resolve_runtime_profile
from agent.runtime_policy import review_policy
from agent.services.cli_session_service import get_cli_session_service
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.integration_registry_service import get_integration_registry_service
from agent.services.repository_registry import get_repository_registry
from agent.services.task_state_machine_service import build_task_state_machine_contract, build_task_status_contract


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
        runtime_preflight = (get_integration_registry_service().list_execution_backends(include_preflight=True).get("preflight") or {})
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "settings": {
                "summary": settings_summary,
                "runtime_telemetry": {
                    "providers": dict(runtime_preflight.get("providers") or {}),
                    "cli_backends": dict(runtime_preflight.get("cli_backends") or {}),
                },
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
        include_task_snapshot: bool,
        benchmark_rows_builder,
        benchmark_recommendation_builder,
        system_health_builder,
        contract_catalog_builder,
        hub_copilot_summary_builder,
        context_policy_summary_builder,
    ) -> dict:
        repos = get_repository_registry()
        teams = [team.model_dump() for team in repos.team_repo.get_all()]
        roles = [role.model_dump() for role in repos.role_repo.get_all()]
        templates = [template.model_dump() for template in repos.template_repo.get_all()]
        agents = [agent.model_dump() for agent in repos.agent_repo.get_all()]
        tasks = [task.model_dump() for task in repos.task_repo.get_all()] if include_task_snapshot else []
        for agent in agents:
            if "token" in agent:
                agent["token"] = "***"

        bench_rows, bench = benchmark_rows_builder(task_kind=benchmark_task_kind, top_n=8)
        valid_task_kind = benchmark_task_kind if benchmark_task_kind in benchmark_task_kinds else "analysis"
        benchmark_recommendation = benchmark_recommendation_builder(task_kind=valid_task_kind, cfg=cfg)
        runtime_preflight = (get_integration_registry_service().list_execution_backends(include_preflight=True).get("preflight") or {})
        contract_catalog = contract_catalog_builder()
        task_status_contract = build_task_status_contract()
        task_state_machine = build_task_state_machine_contract()
        llm_cfg = (cfg or {}).get("llm_config", {}) if isinstance((cfg or {}).get("llm_config"), dict) else {}
        effective_default_provider = (
            str(llm_cfg.get("provider") or cfg.get("default_provider") or "").strip().lower() or None
        )
        effective_default_model = str(llm_cfg.get("model") or cfg.get("default_model") or "").strip() or None
        explicit_override = {
            "provider": str(llm_cfg.get("provider") or "").strip().lower() or None,
            "model": str(llm_cfg.get("model") or "").strip() or None,
            "active": bool(llm_cfg.get("provider") or llm_cfg.get("model")),
            "source": {
                "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
            },
        }
        configured_runtime = {
            "provider": explicit_override["provider"] or effective_default_provider,
            "model": explicit_override["model"] or effective_default_model,
            "mode": "explicit_override" if explicit_override["active"] else "default_config",
            "source": explicit_override["source"] if explicit_override["active"] else {
                "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
            },
        }
        recommended_runtime = benchmark_recommendation.get("recommended") if isinstance(benchmark_recommendation, dict) else None
        codex_runtime = resolve_codex_runtime_config()
        effective_runtime = {
            "provider": (recommended_runtime or {}).get("provider") or configured_runtime["provider"],
            "model": (recommended_runtime or {}).get("model") or configured_runtime["model"],
            "mode": "benchmark_recommendation" if recommended_runtime else configured_runtime["mode"],
            "selection_source": (
                (recommended_runtime or {}).get("selection_source")
                or configured_runtime["source"]["provider"]
            ),
            "benchmark_applied": bool(recommended_runtime),
            "replaces_configured": bool(
                recommended_runtime
                and (
                    (recommended_runtime or {}).get("provider") != configured_runtime["provider"]
                    or (recommended_runtime or {}).get("model") != configured_runtime["model"]
                )
            ),
            "configured": configured_runtime,
        }
        research_backend_cfg = resolve_research_backend_config(agent_cfg=cfg)
        research_backend_review = review_policy(cfg, research_backend_cfg.get("provider"), "research")
        exposure_policy = get_exposure_policy_service().normalize_exposure_policy((cfg or {}).get("exposure_policy"))
        cli_session_mode = (cfg or {}).get("cli_session_mode") if isinstance((cfg or {}).get("cli_session_mode"), dict) else {}
        task_counts = {"total": len(tasks), "completed": 0, "failed": 0, "todo": 0, "in_progress": 0, "blocked": 0}
        recent_timeline = []
        if include_task_snapshot:
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
        return {
            "config": {"effective": cfg, "has_sensitive_redactions": True},
            "system_health": system_health_builder(),
            "contracts": {
                "version": contract_catalog.get("version") or "v1",
                "schema_count": len(contract_catalog.get("schemas") or {}),
                "task_statuses": task_status_contract.model_dump(),
                "task_state_machine": task_state_machine.model_dump(),
            },
            "teams": {"count": len(teams), "items": teams},
            "roles": {"count": len(roles), "items": roles},
            "templates": {"count": len(templates), "items": templates},
            "agents": {"count": len(agents), "items": agents},
            "tasks": {"included": include_task_snapshot, "counts": task_counts, "recent": recent_timeline},
            "llm_configuration": {
                "runtime_profile": resolve_runtime_profile(cfg),
                "defaults": {
                    "provider": effective_default_provider,
                    "model": effective_default_model,
                    "source": {
                        "provider": "agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider",
                        "model": "agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model",
                    },
                },
                "explicit_override": explicit_override,
                "effective_runtime": effective_runtime,
                "hub_copilot": hub_copilot_summary_builder(cfg),
                "context_bundle_policy": context_policy_summary_builder(cfg),
                "research_backend": {
                    "provider": research_backend_cfg.get("provider"),
                    "display_name": research_backend_cfg.get("display_name"),
                    "enabled": bool(research_backend_cfg.get("enabled")),
                    "configured": bool(research_backend_cfg.get("configured")),
                    "mode": research_backend_cfg.get("mode"),
                    "command": research_backend_cfg.get("command"),
                    "working_dir": research_backend_cfg.get("working_dir"),
                    "working_dir_exists": bool(research_backend_cfg.get("working_dir_exists")),
                    "binary_path": research_backend_cfg.get("binary_path"),
                    "binary_available": bool(research_backend_cfg.get("binary_path")),
                    "result_format": research_backend_cfg.get("result_format"),
                    "timeout_seconds": research_backend_cfg.get("timeout_seconds"),
                    "docker_binary": research_backend_cfg.get("docker_binary"),
                    "docker_available": bool(research_backend_cfg.get("docker_available")),
                    "sandbox_image": research_backend_cfg.get("sandbox_image"),
                    "sandbox_network": research_backend_cfg.get("sandbox_network"),
                    "sandbox_workdir": research_backend_cfg.get("sandbox_workdir"),
                    "sandbox_mount_repo": bool(research_backend_cfg.get("sandbox_mount_repo")),
                    "sandbox_read_only": bool(research_backend_cfg.get("sandbox_read_only")),
                    "supported_providers": research_backend_cfg.get("supported_providers") or [],
                    "providers": get_research_backend_preflight(agent_cfg=cfg),
                    "review_policy": research_backend_review,
                },
                "exposure": {
                    "openai_compat": exposure_policy.get("openai_compat") or {},
                    "mcp": exposure_policy.get("mcp") or {},
                },
                "cli_sessions": {
                    "policy": {
                        "enabled": bool(cli_session_mode.get("enabled", False)),
                        "stateful_backends": list(cli_session_mode.get("stateful_backends") or []),
                        "max_turns_per_session": int(cli_session_mode.get("max_turns_per_session") or 40),
                        "max_sessions": int(cli_session_mode.get("max_sessions") or 200),
                        "allow_task_scoped_auto_session": bool(cli_session_mode.get("allow_task_scoped_auto_session", True)),
                    },
                    "runtime": get_cli_session_service().snapshot(),
                },
                "routing_split": {
                    "inference": {
                        "default_provider": effective_default_provider,
                        "default_model": effective_default_model,
                    },
                    "execution": {
                        "default_backend": str(cfg.get("sgpt_execution_backend") or "sgpt").strip().lower(),
                        "codex_target_provider": codex_runtime.get("target_provider"),
                        "codex_target_kind": codex_runtime.get("target_kind"),
                        "codex_target_provider_type": codex_runtime.get("target_provider_type"),
                        "codex_target_base_url": codex_runtime.get("base_url"),
                        "codex_remote_hub": bool(codex_runtime.get("remote_hub")),
                        "codex_instance_id": codex_runtime.get("instance_id"),
                        "codex_max_hops": codex_runtime.get("max_hops"),
                        "codex_diagnostics": list(codex_runtime.get("diagnostics") or []),
                    },
                },
                "runtime_telemetry": {
                    "providers": dict(runtime_preflight.get("providers") or {}),
                    "cli_backends": dict(runtime_preflight.get("cli_backends") or {}),
                    "research_backends": dict(runtime_preflight.get("research_backends") or {}),
                },
            },
            "benchmarks": {
                "task_kind": valid_task_kind,
                "updated_at": bench.get("updated_at"),
                "items": bench_rows,
                "recommendation": benchmark_recommendation,
            },
            "context_timestamp": int(time.time()),
        }


config_read_model_service = ConfigReadModelService()


def get_config_read_model_service() -> ConfigReadModelService:
    return config_read_model_service
