from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.research_backend import get_research_backend_preflight, resolve_research_backend_config
from agent.runtime_profiles import resolve_runtime_profile
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.cli_session_service import get_cli_session_service
from agent.services.service_registry import get_core_services
from agent.services.system_contract_service import get_system_contract_service
from agent.services.system_health_service import build_system_health_payload
from agent.tool_capabilities import build_capability_contract, describe_capabilities, resolve_allowed_tools

from . import shared

read_models_bp = Blueprint("config_read_models", __name__)


def assistant_editable_settings_inventory() -> list[dict]:
    return [
        {"key": "default_provider", "path": "config.default_provider", "type": "enum", "editable": True, "allowed_values": ["ollama", "lmstudio", "openai", "codex", "anthropic"], "endpoint": "POST /config"},
        {"key": "default_model", "path": "config.default_model", "type": "string", "editable": True, "endpoint": "POST /config"},
        {"key": "codex_cli", "path": "config.codex_cli", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "research_backend", "path": "config.research_backend", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "hub_copilot", "path": "config.hub_copilot", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "context_bundle_policy", "path": "config.context_bundle_policy", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "cli_session_mode", "path": "config.cli_session_mode", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "template_agent_name", "path": "config.template_agent_name", "type": "string", "editable": True, "endpoint": "POST /config"},
        {"key": "team_agent_name", "path": "config.team_agent_name", "type": "string", "editable": True, "endpoint": "POST /config"},
        {"key": "quality_gates", "path": "config.quality_gates", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "exposure_policy", "path": "config.exposure_policy", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "benchmark_retention", "path": "config.benchmark_retention", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "benchmark_identity_precedence", "path": "config.benchmark_identity_precedence", "type": "object", "editable": True, "endpoint": "POST /config"},
        {"key": "http_timeout", "path": "config.http_timeout", "type": "integer", "editable": True, "min": 1, "endpoint": "POST /config"},
        {"key": "command_timeout", "path": "config.command_timeout", "type": "integer", "editable": True, "min": 1, "endpoint": "POST /config"},
        {"key": "agent_offline_timeout", "path": "config.agent_offline_timeout", "type": "integer", "editable": True, "min": 10, "endpoint": "POST /config"},
        {"key": "log_level", "path": "config.log_level", "type": "enum", "editable": True, "allowed_values": ["DEBUG", "INFO", "WARNING", "ERROR"], "endpoint": "POST /config"},
        {
            "key": "runtime_profile",
            "path": "config.runtime_profile",
            "type": "enum",
            "editable": True,
            "allowed_values": ["local-dev", "trusted-lab", "compose-safe", "distributed-strict"],
            "endpoint": "POST /config",
        },
        {"key": "templates", "path": "templates", "type": "collection", "editable": True, "endpoint": "/templates"},
        {"key": "teams", "path": "teams", "type": "collection", "editable": True, "endpoint": "/teams"},
        {"key": "team_types", "path": "teams.types", "type": "collection", "editable": True, "endpoint": "/teams/types"},
        {"key": "roles", "path": "teams.roles", "type": "collection", "editable": True, "endpoint": "/teams/roles"},
        {"key": "autopilot", "path": "tasks.autopilot", "type": "object", "editable": True, "endpoint": "/tasks/autopilot/start|stop|tick"},
        {"key": "auto_planner", "path": "tasks.auto_planner", "type": "object", "editable": True, "endpoint": "/tasks/auto-planner/configure"},
        {"key": "triggers", "path": "triggers", "type": "object", "editable": True, "endpoint": "/triggers/configure"},
    ]


def assistant_settings_summary(cfg: dict, teams: list[dict], templates: list[dict]) -> dict:
    quality_gates = (cfg or {}).get("quality_gates", {}) or {}
    research_cfg = resolve_research_backend_config(agent_cfg=cfg)
    codex_cli = cfg.get("codex_cli") if isinstance(cfg.get("codex_cli"), dict) else {}
    review_cfg = cfg.get("review_policy") if isinstance(cfg.get("review_policy"), dict) else {}
    risk_cfg = cfg.get("execution_risk_policy") if isinstance(cfg.get("execution_risk_policy"), dict) else {}
    exposure_policy = get_exposure_policy_service().normalize_exposure_policy(cfg.get("exposure_policy"))
    cli_session_mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
    return {
        "llm": {
            "default_provider": cfg.get("default_provider"),
            "default_model": cfg.get("default_model"),
            "template_agent_name": cfg.get("template_agent_name"),
            "team_agent_name": cfg.get("team_agent_name"),
            "codex_cli": {
                "base_url": codex_cli.get("base_url"),
                "api_key_profile": codex_cli.get("api_key_profile"),
                "prefer_lmstudio": codex_cli.get("prefer_lmstudio"),
            },
            "research_backend": {
                "provider": research_cfg.get("provider"),
                "enabled": research_cfg.get("enabled"),
                "configured": bool(research_cfg.get("configured")),
                "mode": research_cfg.get("mode"),
                "command": research_cfg.get("command"),
                "working_dir": research_cfg.get("working_dir"),
                "working_dir_exists": bool(research_cfg.get("working_dir_exists")),
                "binary_path": research_cfg.get("binary_path"),
                "binary_available": bool(research_cfg.get("binary_path")),
                "result_format": research_cfg.get("result_format"),
                "timeout_seconds": research_cfg.get("timeout_seconds"),
                "docker_binary": research_cfg.get("docker_binary"),
                "docker_available": bool(research_cfg.get("docker_available")),
                "sandbox_image": research_cfg.get("sandbox_image"),
                "sandbox_network": research_cfg.get("sandbox_network"),
                "sandbox_workdir": research_cfg.get("sandbox_workdir"),
                "sandbox_mount_repo": bool(research_cfg.get("sandbox_mount_repo")),
                "sandbox_read_only": bool(research_cfg.get("sandbox_read_only")),
                "supported_providers": research_cfg.get("supported_providers") or [],
                "providers": get_research_backend_preflight(agent_cfg=cfg),
            },
            "hub_copilot": shared.hub_copilot_settings_summary(cfg),
            "context_bundle_policy": shared.context_bundle_policy_settings_summary(cfg),
            "cli_session_mode": {
                "enabled": bool(cli_session_mode.get("enabled", False)),
                "stateful_backends": list(cli_session_mode.get("stateful_backends") or []),
                "max_turns_per_session": int(cli_session_mode.get("max_turns_per_session") or 40),
                "max_sessions": int(cli_session_mode.get("max_sessions") or 200),
                "allow_task_scoped_auto_session": bool(cli_session_mode.get("allow_task_scoped_auto_session", True)),
                "runtime": get_cli_session_service().snapshot(),
            },
        },
        "system": {
            "log_level": cfg.get("log_level"),
            "http_timeout": cfg.get("http_timeout"),
            "command_timeout": cfg.get("command_timeout"),
            "agent_offline_timeout": cfg.get("agent_offline_timeout"),
            "runtime_profile": resolve_runtime_profile(cfg),
        },
        "quality_gates": {
            "enabled": quality_gates.get("enabled"),
            "autopilot_enforce": quality_gates.get("autopilot_enforce"),
            "min_output_chars": quality_gates.get("min_output_chars"),
        },
        "governance": {
            "review_policy": {
                "enabled": bool(review_cfg.get("enabled", True)),
                "policy_version": review_cfg.get("policy_version") or "review-v1",
                "min_risk_level_for_review": review_cfg.get("min_risk_level_for_review") or "high",
                "terminal_risk_level": review_cfg.get("terminal_risk_level") or "high",
                "file_access_risk_level": review_cfg.get("file_access_risk_level") or "medium",
            },
            "execution_risk_policy": {
                "enabled": bool(risk_cfg.get("enabled", True)),
                "default_action": risk_cfg.get("default_action") or "deny",
                "deny_risk_levels": list(risk_cfg.get("deny_risk_levels") or ["high", "critical"]),
                "review_risk_levels": list(risk_cfg.get("review_risk_levels") or ["medium", "high", "critical"]),
                "task_scoped_only": bool(risk_cfg.get("task_scoped_only", True)),
            },
            "exposure_policy": exposure_policy,
        },
        "counts": {"teams": len(teams), "templates": len(templates)},
    }


def assistant_automation_snapshot() -> dict:
    from agent.services.automation_snapshot_service import get_automation_snapshot_service

    return get_automation_snapshot_service().build_snapshot()


@read_models_bp.route("/assistant/read-model", methods=["GET"])
@check_auth
def assistant_read_model():
    cfg = shared.sanitize_assistant_config(current_app.config.get("AGENT_CONFIG", {}) or {})
    return api_response(
        data=get_core_services().config_read_model_service.assistant_read_model(
            cfg=cfg,
            is_admin=bool(getattr(g, "is_admin", False)),
            capability_contract_builder=build_capability_contract,
            allowed_tools_resolver=resolve_allowed_tools,
            capabilities_describer=describe_capabilities,
            settings_inventory_builder=assistant_editable_settings_inventory,
            settings_summary_builder=assistant_settings_summary,
            automation_snapshot_builder=assistant_automation_snapshot,
        )
    )


@read_models_bp.route("/dashboard/read-model", methods=["GET"])
@check_auth
def dashboard_read_model():
    cfg = shared.sanitize_assistant_config(current_app.config.get("AGENT_CONFIG", {}) or {})
    benchmark_task_kind = str(request.args.get("benchmark_task_kind") or "analysis").strip().lower()
    include_task_snapshot = shared.parse_bool_query_flag(request.args.get("include_task_snapshot"))
    return api_response(
        data=get_core_services().config_read_model_service.dashboard_read_model(
            cfg=cfg,
            benchmark_task_kind=benchmark_task_kind,
            benchmark_task_kinds=shared._BENCH_TASK_KINDS,
            include_task_snapshot=include_task_snapshot,
            benchmark_rows_builder=shared.benchmark_rows_for_task,
            benchmark_recommendation_builder=shared.dashboard_benchmark_recommendation,
            system_health_builder=lambda: build_system_health_payload(current_app, basic_mode=False),
            contract_catalog_builder=lambda: get_system_contract_service().build_contract_catalog(),
            hub_copilot_summary_builder=shared.hub_copilot_settings_summary,
            context_policy_summary_builder=shared.context_bundle_policy_settings_summary,
        )
    )
