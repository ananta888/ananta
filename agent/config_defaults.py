import logging
import json
import os
from flask import Flask
from agent.config import settings
from agent.model_selection import normalize_legacy_model_name
from agent.runtime_profiles import runtime_profile_catalog

def _provider_alias(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    return "openai" if value == "codex" else value


def _default_opencode_model() -> str | None:
    configured = str(getattr(settings, "opencode_default_model", None) or "").strip()
    if configured and configured != "opencode/glm-5-free":
        return normalize_legacy_model_name(configured, provider=str(settings.default_provider or "").strip().lower())
    if str(settings.default_provider or "").strip().lower() == "ollama":
        return "qwen2.5-coder:7b"
    return configured or None

def build_default_agent_config() -> dict:
    opencode_default_model = _default_opencode_model()
    return {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "auth_provider": settings.auth_provider,
        "opencode_default_model": opencode_default_model,
        "provider": settings.default_provider,
        "model": settings.default_model,
        "llm_config": {
            "provider": settings.default_provider,
            "model": settings.default_model,
            "base_url": (
                settings.lmstudio_url
                if settings.default_provider == "lmstudio"
                else (settings.ollama_url if settings.default_provider == "ollama" else None)
            ),
            "lmstudio_api_mode": settings.lmstudio_api_mode,
        },
        "voice_runtime": {
            "provider": settings.voice_provider,
            "base_url": settings.voice_runtime_url,
            "model": settings.voice_model,
            "fallback_model": settings.voice_fallback_model,
            "timeout_sec": int(settings.voice_timeout_sec or 120),
            "max_audio_mb": int(settings.voice_max_audio_mb or 25),
            "direct_client_access": bool(settings.voice_direct_client_access),
            "device": settings.voice_runtime_device,
            "model_path": settings.voice_runtime_model_path,
            "enable_streaming": bool(settings.voice_enable_streaming),
            "store_audio": bool(settings.voice_store_audio),
        },
        "max_summary_length": 500,
        "quality_gates": {
            "enabled": True,
            "autopilot_enforce": False,
            "coding_keywords": ["code", "implement", "fix", "refactor", "bug", "test", "feature", "endpoint"],
            "required_output_markers_for_coding": ["test", "pytest", "passed", "success", "lint", "ok"],
            "min_output_chars": 8,
        },
        "autonomous_guardrails": {
            "enabled": True,
            "max_runtime_seconds": 21600,
            "max_ticks_total": 5000,
            "max_dispatched_total": 50000,
        },
        "llm_tool_guardrails": {
            "enabled": True,
            "max_tool_calls_per_request": 10,
            "max_external_calls_per_request": 6,
            "max_estimated_cost_units_per_request": 40,
            "max_tokens_per_request": 6000,
            "chars_per_token_estimate": 4,
            "class_limits": {"read": 8, "write": 6, "admin": 1},
            "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
            "external_classes": ["write", "admin"],
            "tool_classes": {
                # hub/orchestration tools
                "list_teams": "read",
                "list_roles": "read",
                "list_agents": "read",
                "list_templates": "read",
                "analyze_logs": "read",
                "read_agent_logs": "read",
                "create_team": "write",
                "assign_role": "write",
                "ensure_team_templates": "write",
                "create_template": "write",
                "update_template": "write",
                "delete_template": "write",
                "upsert_team_type": "write",
                "delete_team_type": "write",
                "upsert_role": "write",
                "delete_role": "write",
                "link_role_to_team_type": "write",
                "unlink_role_from_team_type": "write",
                "set_role_template_mapping": "write",
                "upsert_team": "write",
                "delete_team": "write",
                "activate_team": "write",
                "configure_auto_planner": "admin",
                "configure_triggers": "admin",
                "set_autopilot_state": "admin",
                "update_config": "admin",
                # worker file/shell tools
                "file_read": "read",
                "file_list": "read",
                "file_write": "write",
                "file_patch": "write",
                "shell_execute": "write",
                "git_status": "read",
                "git_diff": "read",
                "git_log": "read",
                "git_commit": "write",
                "web_fetch": "read",
                "web_search": "read",
                "doc_extract": "read",
            },
        },
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.2,
            "retry_max_backoff_seconds": 5.0,
            "retry_jitter_factor": 0.2,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
        "autopilot": {
            "async_dispatch_enabled": True,
        },
        "autopilot_strategy_max_attempts": 3,
        "autopilot_strategy_retry_delay_seconds": 20,
        # Use hard guard as recovery (todo) instead of dead-end review gate.
        "autopilot_task_propose_hard_guard_status": "todo",
        "autopilot_strategy_fallback_models": [opencode_default_model] if opencode_default_model else [],
        "autopilot_strategy_temperature_profiles": [0.2, 0.5, 0.8],
        "proposal_budget": {
            "max_total_seconds": 90,
            "max_llm_calls": 2,
            "max_strategy_attempts": 2,
            "allow_parallel_strategy_race": False,
        },
        "llm_profile_policy": {
            # Synthetic llm_call_profile fallback is opt-in. When disabled,
            # diagnostics only see real provider call telemetry.
            "allow_synthetic_fallback": False,
        },
        "goal_scoped_config_enabled": True,
        "goal_scoped_config_enforce_snapshot": False,
        "adaptive_model_routing_enabled": True,
        "adaptive_model_routing_min_samples": 3,
        "adaptive_model_routing_top_k": 3,
        "execution_fallback_policy": {
            "allow_hub_worker_fallback": True,
            "escalate_on_fallback_block": True,
            "fallback_block_status": "blocked",
        },
        "routing_fallback_policy": {
            "enabled": True,
            "allow_static_providers": True,
            "allow_local_backends": True,
            "allow_remote_hubs": True,
            "allow_stateful_cli": True,
            "allow_stateless_generation": True,
            "fallback_order": [
                "request_override",
                "task_benchmark",
                "configured_default",
                "local_runtime_probe",
            ],
            "unavailable_action": "mark_unavailable",
        },
        "result_memory_policy": {
            "enabled": True,
            "create_followup_artifact": True,
            "retrieval_document_max_chars": 2200,
            "raw_history_max_chars": 12000,
            "archive_raw_output": False,
            "neighbor_file_terms_enabled": True,
        },
        "remote_federation_policy": {
            "enabled": True,
            "default_trust_level": "partner",
            "allowed_operations": ["models", "chat"],
            "allow_artifact_access": False,
            "allow_file_access": False,
            "require_provenance": True,
            "max_hops": 3,
        },
        "exposure_policy": {
            "openai_compat": {
                "enabled": True,
                "allow_agent_auth": True,
                "allow_user_auth": True,
                "require_admin_for_user_auth": True,
                "allow_files_api": True,
                "emit_audit_events": True,
                "instance_id": None,
                "max_hops": 3,
            },
            "mcp": {
                "enabled": False,
                "allow_agent_auth": False,
                "allow_user_auth": False,
                "require_admin_for_user_auth": True,
                "emit_audit_events": True,
            },
            "remote_hubs": {
                "enabled": True,
                "require_admin_for_user_auth": True,
                "emit_audit_events": True,
                "max_hops": 3,
            },
            "voice": {
                "enabled": True,
                "allow_agent_auth": False,
                "allow_user_auth": True,
                "require_admin_for_user_auth": False,
                "require_explicit_approval_for_goal": True,
                "emit_audit_events": True,
            },
        },
        "platform_mode": "local-dev",
        "terminal_policy": {
            "enabled": False,
            "allow_read": False,
            "allow_interactive": False,
            "require_admin": True,
            "emit_audit_events": True,
            "max_session_seconds": 1800,
            "idle_timeout_seconds": 300,
            "input_preview_max_chars": 120,
            "allowed_roles": [],
            "allowed_cidrs": [],
        },
        "evolution": {
            "enabled": True,
            "default_provider": None,
            "analyze_only": True,
            "validate_allowed": True,
            "apply_allowed": False,
            "auto_triggers_enabled": False,
            "manual_triggers_enabled": True,
            "max_manual_analyses_per_task": 20,
            "require_review_before_apply": True,
            "max_raw_payload_bytes": 32768,
            "provider_overrides": {
                "evolver": {
                    "enabled": False,
                    "provider_name": "evolver",
                    "base_url": None,
                    "analyze_path": "/evolution/analyze",
                    "timeout_seconds": 30.0,
                    "connect_timeout_seconds": None,
                    "read_timeout_seconds": None,
                    "max_response_bytes": 1048576,
                    "retry_count": 0,
                    "retry_backoff_seconds": 0.0,
                    "allowed_hosts": [],
                    "force_analyze_only": True,
                    "default": False,
                    "replace": True,
                    "version": "unknown",
                }
            },
        },
        "goal_plan_limits": {
            "max_plan_nodes": 8,
            "max_plan_depth": 8,
        },
        "task_kind_execution_policies": {
            "coding": {
                "command_timeout": 90,
                "command_retries": 1,
                "command_retry_delay": 2,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 15,
            },
            "analysis": {
                "command_timeout": 60,
                "command_retries": 0,
                "command_retry_delay": 1,
            },
            "doc": {
                "command_timeout": 45,
                "command_retries": 0,
                "command_retry_delay": 1,
            },
            "ops": {
                "command_timeout": 120,
                "command_retries": 2,
                "command_retry_delay": 3,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 20,
            },
            "research": {
                "command_timeout": 180,
                "command_retries": 1,
                "command_retry_delay": 2,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 20,
            },
        },
        "llm_pricing": {
            "default": {"cost_per_1k_tokens": 0.0},
        },
        "review_policy": {
            "enabled": True,
            "policy_version": "review-v2",
            "research_backends": ["deerflow", "ananta_research"],
            "task_kinds": ["research"],
            "min_risk_level_for_review": "high",
            "terminal_risk_level": "high",
            "file_access_risk_level": "medium",
        },
        "execution_risk_policy": {
            "enabled": True,
            "default_action": "deny",
            "deny_risk_levels": ["critical"],
            "review_risk_levels": ["high", "critical"],
            "task_scoped_only": True,
            "require_terminal_capability_for_command": False,
            "terminal_capability_name": "terminal",
        },
        "autopilot_security_policies": {
            "safe": {
                "max_concurrency_cap": 1,
                "execute_timeout": 45,
                "execute_retries": 0,
                "allowed_tool_classes": ["read"],
            },
            "balanced": {
                "max_concurrency_cap": 4,
                "execute_timeout": 60,
                "execute_retries": 1,
                "allowed_tool_classes": ["read", "write"],
            },
            "aggressive": {
                "max_concurrency_cap": 4,
                "execute_timeout": 120,
                "execute_retries": 2,
                "allowed_tool_classes": ["read", "write", "admin", "unknown"],
            },
        },
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "ananta-worker",
            "backend_parallel_limits": {
                "sgpt": 1,
                "ananta-worker": 1,
                "codex": 1,
                "opencode": 1,
                "aider": 1,
                "mistral_code": 1,
            },
            "task_kind_backend": {
                "coding": "ananta-worker",
                "analysis": "ananta-worker",
                "doc": "ananta-worker",
                "ops": "ananta-worker",
                "research": "deerflow",
            },
            "research_capability_backend": {},
        },
        "task_propose_timeout_seconds": 300,
        "codex_cli": {
            "base_url": None,
            "api_key_profile": None,
            "prefer_lmstudio": True,
        },
        "cli_session_mode": {
            "enabled": False,
            "stateful_backends": ["opencode", "codex"],
            "max_turns_per_session": 40,
            "max_sessions": 200,
            "allow_task_scoped_auto_session": True,
            "reuse_scope": "task",
            "native_opencode_sessions": False,
        },
        "opencode_runtime": {
            "tool_mode": "toolless" if str(settings.default_provider or "").strip().lower() == "ollama" else "full",
            "execution_mode": (os.environ.get("ANANTA_OPENCODE_EXECUTION_MODE") or "live_terminal").strip().lower() or "live_terminal",
            "interactive_launch_mode": (
                os.environ.get("ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE") or "run"
            ).strip().lower() or "run",
            "target_provider": (
                str(settings.default_provider or "").strip().lower()
                if str(settings.default_provider or "").strip().lower() in {"ollama", "lmstudio"}
                else None
            ),
        },
        "worker_runtime": {
            "workspace_root": os.environ.get("ANANTA_WORKSPACE_ROOT") or None,
            "workspace_reuse_mode": "goal_worker",
            "default_execution_profile": "balanced",
            "todo_contract": {
                "enabled": True,
                "planner_llm_enabled": True,
                "planner_llm_timeout_seconds": 12,
                "planner_llm_retry_attempts": 1,
                "max_tasks": 6,
                "max_steps": 30,
                "enforce_artifacts": True,
                "default_executor_kind": "ananta_worker",
                "execution_mode": "assistant_execute",
                "provider": None,
                "model": None,
                "base_url": None,
                "api_key": None,
            },
            "codecompass_retrieval": {
                "codecompass_fts": bool(settings.codecompass_fts_enabled),
                "codecompass_vector": bool(settings.codecompass_vector_enabled),
                "codecompass_graph": bool(settings.codecompass_graph_enabled),
                "codecompass_relation_expansion": bool(settings.codecompass_relation_expansion_enabled),
            },
            "native_worker_runtime": {
                "enabled": True,
                "fallback_backend": "sgpt",
            },
            "codecompass_auto_bundle": {
                "enabled": False,
                "task_kinds": [],
            },
        },
        "git_workspace": {
            "enabled": False,
            "remote_url": None,
            "branch_strategy": "goal",
            "merge_strategy": "squash",
            "auto_commit": False,
        },
        "workspace_context_policy": {
            "scope_mode": "full",
            "max_files": 200,
            "sensitivity_ceiling": "confidential",
            "allowed_paths": [],
            "codecompass_profile": None,
        },
        "worker_parallelism": {
            "schema": "ananta_worker_parallelism_config_v1",
            "enabled": True,
            "resource_caps_are_authoritative": True,
            "effective_concurrency_rule": "min(security_policy_cap, worker_capacity, runtime_capacity, ollama_model_capacity)",
            "ollama": {
                "enabled": True,
                "default_endpoint": "http://ollama:11434",
                "model_defaults": {
                    "max_parallel_requests": 4,
                    "queue_limit": 64,
                    "request_timeout_seconds": 300,
                    "slot_lease_seconds": 600,
                    "backpressure": "queue_then_reject",
                    "slot_strategy": "fifo_with_fairness",
                },
                "models": {
                    "ananta-default:latest": {
                        "max_parallel_requests": 4,
                        "queue_limit": 64,
                        "preferred_for": ["analysis", "planning", "repair.preview", "code_review"],
                    }
                },
            },
            "worker_pool": {
                "enabled": True,
                "minimum_local_worker_containers": 2,
                "worker_defaults": {
                    "max_parallel_tasks": 4,
                    "queue_limit": 32,
                    "heartbeat_timeout_seconds": 30,
                    "slot_lease_seconds": 600,
                },
                "kinds": {
                    "native_ananta_worker": {
                        "enabled": True,
                        "container_replicas": 2,
                        "max_parallel_tasks_per_container": 4,
                        "subworkers": {
                            "enabled": True,
                            "max_children_per_parent": 4,
                            "max_depth": 2,
                            "capability_subset_required": True,
                            "context_subset_required": True,
                        },
                    },
                    "opencode": {
                        "enabled": True,
                        "container_replicas": 2,
                        "max_parallel_tasks_per_container": 2,
                        "process_pool": {
                            "enabled": True,
                            "max_processes": 2,
                        },
                    },
                    "hermes": {
                        "enabled": True,
                        "max_parallel_tasks_per_container": 2,
                    },
                },
            },
            "scheduling": {
                "strategy": "policy_then_capacity_then_least_loaded",
                "respect_context_policy": True,
                "respect_runtime_policy": True,
                "prefer_local": True,
                "avoid_oversubscribing_ollama": True,
                "queued_job_revalidation": True,
                "fairness": {
                    "enabled": True,
                    "max_running_jobs_per_parent_task": 4,
                    "max_queued_jobs_per_parent_task": 16,
                },
            },
        },
        "knowledge_context": {
            "auto_include": {
                "task_kinds": ["coding", "bugfix", "refactor", "analysis"],
                "knowledge_collection_ids": [],
                "artifact_ids": [],
                "repo_scope_refs": [],
            },
            "auto_index_paths": {
                "enabled": False,
                "profile": "default",
                "task_kinds": ["coding", "bugfix", "refactor", "analysis"],
            },
        },
        "planning": {
            # "llm"      → LLM (Ollama) always generates the task plan
            # "template" → fixed template/blueprint first, LLM fallback if no match
            # "auto"     → template first, LLM fallback (default)
            "default_strategy": "auto",
        },
        "planning_policy": {
            "delegated_planning_enabled": False,
            "allowed_planner_roles": ["planning-agent", "planner"],
            "require_review": True,
            "allow_remote_planners": False,
            "max_nodes": 8,
            "max_depth": 8,
            "timeout_seconds": 600,
            "max_output_tokens": 900,
            "segmented_planning_enabled": True,
            "segment_context_chars": 2400,
            "max_segments": 3,
            "preferred_output_format": "json",
            "selective_repair_rounds": 2,
            "validation_profiles": {
                "new_software_project": {
                    "min_total_tasks": 4,
                    "required_categories": {
                        "analysis": 1,
                        "infrastructure": 1,
                        "implementation": 1,
                        "tests": 1,
                        "review": 1,
                    },
                    "max_generic_tasks": 4,
                },
                "generic": {
                    "min_total_tasks": 3,
                    "required_categories": {"implementation": 1},
                    "max_generic_tasks": 1,
                },
            },
            "planner_prompt_evolution": {
                "enabled": True,
                "min_repair_attempts": 2,
            },
            "learning_loop": {
                "enabled": True,
                "interval_seconds": 300,
                "lookback_runs": 120,
                "min_runs": 3,
                "min_failures": 1,
                "min_parse_success_rate": 0.7,
                "min_validation_success_rate": 0.7,
                "min_materialization_success_rate": 0.6,
                "max_repair_rate": 0.8,
                "candidate_activation_threshold": 0.62,
                "rollback_threshold": 0.45,
                "freeze_minutes": 30,
                "canary_window_runs": 5,
                "auto_activate": True,
                "require_review_before_activate": False,
            },
            "default_runtime_profile": "lmstudio_laptop",
            "runtime_profiles": {
                "lmstudio_laptop": {
                    "timeout_seconds": 480,
                    "max_output_tokens": 900,
                    "retry_attempts": 1,
                    "retry_backoff_seconds": 1.0,
                    "segmented_planning_enabled": True,
                    "segment_context_chars": 1400,
                    "max_segments": 2,
                    "preferred_output_format": "json",
                },
                "lmstudio_laptop_thinking": {
                    "timeout_seconds": 300,
                    "max_output_tokens": 4000,
                    "retry_attempts": 1,
                    "retry_backoff_seconds": 2.0,
                    "segmented_planning_enabled": False,
                    "segment_context_chars": 2000,
                    "max_segments": 1,
                    "preferred_output_format": "json",
                },
                "ollama_rtx3080": {
                    "timeout_seconds": 180,
                    "max_output_tokens": 768,
                    "retry_attempts": 2,
                    "retry_backoff_seconds": 0.75,
                    "segmented_planning_enabled": True,
                    "segment_context_chars": 3200,
                    "max_segments": 3,
                    "preferred_output_format": "markdown",
                },
            },
        },
        "research_backend": {
            "provider": "deerflow",
            "enabled": False,
            "mode": "cli",
            "command": "python main.py {prompt}",
            "working_dir": None,
            "timeout_seconds": 900,
            "result_format": "markdown",
            "docker_binary": "docker",
            "sandbox_image": None,
            "sandbox_network": "none",
            "sandbox_workdir": "/workspace",
            "sandbox_mount_repo": True,
            "sandbox_read_only": True,
            "sandbox_tmp_dir": "/tmp/ananta-research",
        },
        # GOV-050: expliziter Governance-Modus als Produktentscheidung (additiv).
        # Harte Policy-Durchsetzung bleibt weiterhin in expliziten Policy-Bloecken verankert,
        # um versteckte Seiteneffekte zu vermeiden.
        "governance_mode": "balanced",
        "runtime_profile": "local-dev",
        "runtime_profile_catalog": runtime_profile_catalog(),
    }

def merge_db_config_overrides(default_cfg: dict) -> None:
    try:
        import json
        from agent.repository import config_repo
        from agent.services.config_service import unwrap_config

        db_configs = config_repo.get_all()
        reserved_keys = {"status", "message", "code"}
        for cfg in db_configs:
            if cfg.key in reserved_keys:
                continue
            try:
                default_cfg[cfg.key] = unwrap_config(json.loads(cfg.value_json))
            except Exception:
                default_cfg[cfg.key] = cfg.value_json
    except Exception as e:
        logging.warning(f"Konnte Konfiguration nicht aus DB laden: {e}. Nutze Fallback.")

def apply_env_config_overrides(cfg: dict) -> None:
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    forced_execution_mode = str(os.environ.get("ANANTA_OPENCODE_EXECUTION_MODE") or "").strip().lower()
    if forced_execution_mode in {"backend", "live_terminal", "interactive_terminal"}:
        runtime_cfg["execution_mode"] = forced_execution_mode
    forced_launch_mode = str(os.environ.get("ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE") or "").strip().lower()
    if forced_launch_mode in {"run", "tui"}:
        runtime_cfg["interactive_launch_mode"] = forced_launch_mode
    forced_target_provider = str(os.environ.get("ANANTA_OPENCODE_TARGET_PROVIDER") or "").strip().lower()
    if forced_target_provider in {"ollama", "lmstudio"}:
        runtime_cfg["target_provider"] = forced_target_provider
    elif "target_provider" not in runtime_cfg:
        runtime_cfg["target_provider"] = (
            str(settings.default_provider or "").strip().lower()
            if str(settings.default_provider or "").strip().lower() in {"ollama", "lmstudio"}
            else None
        )
    if runtime_cfg:
        cfg["opencode_runtime"] = runtime_cfg

    parallel_cfg = cfg.get("worker_parallelism") if isinstance(cfg.get("worker_parallelism"), dict) else {}
    if parallel_cfg:
        ollama_cfg = parallel_cfg.get("ollama") if isinstance(parallel_cfg.get("ollama"), dict) else {}
        model_defaults = ollama_cfg.get("model_defaults") if isinstance(ollama_cfg.get("model_defaults"), dict) else {}
        worker_pool = parallel_cfg.get("worker_pool") if isinstance(parallel_cfg.get("worker_pool"), dict) else {}
        worker_defaults = worker_pool.get("worker_defaults") if isinstance(worker_pool.get("worker_defaults"), dict) else {}
        kinds = worker_pool.get("kinds") if isinstance(worker_pool.get("kinds"), dict) else {}
        native_kind = kinds.get("native_ananta_worker") if isinstance(kinds.get("native_ananta_worker"), dict) else {}
        subworkers = native_kind.get("subworkers") if isinstance(native_kind.get("subworkers"), dict) else {}

        if "ANANTA_WORKER_POOL_ENABLED" in os.environ:
            parallel_cfg["enabled"] = str(os.environ.get("ANANTA_WORKER_POOL_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
        if "ANANTA_OLLAMA_MAX_PARALLEL" in os.environ:
            try:
                model_defaults["max_parallel_requests"] = max(1, int(os.environ.get("ANANTA_OLLAMA_MAX_PARALLEL") or 4))
            except Exception:
                pass
        if "ANANTA_WORKER_MAX_PARALLEL_TASKS" in os.environ:
            try:
                worker_defaults["max_parallel_tasks"] = max(1, int(os.environ.get("ANANTA_WORKER_MAX_PARALLEL_TASKS") or 4))
                native_kind["max_parallel_tasks_per_container"] = worker_defaults["max_parallel_tasks"]
            except Exception:
                pass
        if "ANANTA_SUBWORKER_MAX_CHILDREN" in os.environ:
            try:
                subworkers["max_children_per_parent"] = max(1, int(os.environ.get("ANANTA_SUBWORKER_MAX_CHILDREN") or 4))
            except Exception:
                pass

        if model_defaults:
            ollama_cfg["model_defaults"] = model_defaults
        if subworkers:
            native_kind["subworkers"] = subworkers
        if native_kind:
            kinds["native_ananta_worker"] = native_kind
        if kinds:
            worker_pool["kinds"] = kinds
        if worker_defaults:
            worker_pool["worker_defaults"] = worker_defaults
        if worker_pool:
            parallel_cfg["worker_pool"] = worker_pool
        if ollama_cfg:
            parallel_cfg["ollama"] = ollama_cfg
        cfg["worker_parallelism"] = parallel_cfg

    evolution_cfg = cfg.get("evolution") if isinstance(cfg.get("evolution"), dict) else {}
    provider_overrides = evolution_cfg.get("provider_overrides")
    if not isinstance(provider_overrides, dict):
        provider_overrides = {}
    evolver_cfg = dict(provider_overrides.get("evolver") or {})
    evolver_headers = getattr(settings, "evolver_headers", None)
    parsed_evolver_headers = {}
    if evolver_headers:
        try:
            raw_headers = json.loads(evolver_headers)
            if isinstance(raw_headers, dict):
                parsed_evolver_headers = {str(key): str(value) for key, value in raw_headers.items()}
        except Exception:
            parsed_evolver_headers = {}
    allowed_hosts = [
        item.strip()
        for item in str(getattr(settings, "evolver_allowed_hosts", "") or "").split(",")
        if item.strip()
    ]
    env_to_key = {
        "EVOLVER_ENABLED": ("enabled", bool(getattr(settings, "evolver_enabled", False))),
        "EVOLVER_BASE_URL": ("base_url", getattr(settings, "evolver_base_url", None)),
        "EVOLVER_ANALYZE_PATH": ("analyze_path", getattr(settings, "evolver_analyze_path", "/evolution/analyze")),
        "EVOLVER_HEALTH_PATH": ("health_path", getattr(settings, "evolver_health_path", None)),
        "EVOLVER_TIMEOUT_SECONDS": (
            "timeout_seconds",
            float(getattr(settings, "evolver_timeout_seconds", 30.0) or 30.0),
        ),
        "EVOLVER_CONNECT_TIMEOUT_SECONDS": (
            "connect_timeout_seconds",
            getattr(settings, "evolver_connect_timeout_seconds", None),
        ),
        "EVOLVER_READ_TIMEOUT_SECONDS": (
            "read_timeout_seconds",
            getattr(settings, "evolver_read_timeout_seconds", None),
        ),
        "EVOLVER_MAX_RESPONSE_BYTES": (
            "max_response_bytes",
            int(getattr(settings, "evolver_max_response_bytes", 1048576) or 1048576),
        ),
        "EVOLVER_RETRY_COUNT": ("retry_count", int(getattr(settings, "evolver_retry_count", 0) or 0)),
        "EVOLVER_RETRY_BACKOFF_SECONDS": (
            "retry_backoff_seconds",
            float(getattr(settings, "evolver_retry_backoff_seconds", 0.0) or 0.0),
        ),
        "EVOLVER_BEARER_TOKEN": ("bearer_token", getattr(settings, "evolver_bearer_token", None)),
        "EVOLVER_HEADERS": ("headers", parsed_evolver_headers),
        "EVOLVER_ALLOWED_HOSTS": ("allowed_hosts", allowed_hosts),
        "EVOLVER_FORCE_ANALYZE_ONLY": (
            "force_analyze_only",
            bool(getattr(settings, "evolver_force_analyze_only", True)),
        ),
        "EVOLVER_DEFAULT": ("default", bool(getattr(settings, "evolver_default", False))),
        "EVOLVER_VERSION": ("version", getattr(settings, "evolver_version", "unknown")),
    }
    for env_name, (key, value) in env_to_key.items():
        if env_name in os.environ:
            evolver_cfg[key] = value
    provider_overrides["evolver"] = evolver_cfg
    evolution_cfg["provider_overrides"] = provider_overrides
    if evolver_cfg["enabled"] and evolver_cfg["default"]:
        evolution_cfg["default_provider"] = evolver_cfg.get("provider_name") or "evolver"
    cfg["evolution"] = evolution_cfg

def _sync_default_provider_settings(lc: dict) -> str | None:
    prov = lc.get("provider")
    if prov and hasattr(settings, "default_provider"):
        settings.default_provider = prov
    if lc.get("model") and hasattr(settings, "default_model"):
        settings.default_model = lc.get("model")
    if lc.get("lmstudio_api_mode") and hasattr(settings, "lmstudio_api_mode"):
        settings.lmstudio_api_mode = lc.get("lmstudio_api_mode")
    return prov

def _sync_provider_connection_settings(app: Flask, prov: str, lc: dict) -> None:
    effective_provider = _provider_alias(prov)
    if lc.get("base_url"):
        app.config["PROVIDER_URLS"][prov] = lc.get("base_url")
        if prov == "codex":
            app.config["PROVIDER_URLS"]["openai"] = lc.get("base_url")
        url_attr = f"{effective_provider}_url"
        if hasattr(settings, url_attr):
            setattr(settings, url_attr, lc.get("base_url"))

    if not lc.get("api_key"):
        return
    key_attr = f"{effective_provider}_api_key"
    if hasattr(settings, key_attr):
        setattr(settings, key_attr, lc.get("api_key"))
    if prov in {"openai", "codex"}:
        app.config["OPENAI_API_KEY"] = lc.get("api_key")
    elif prov == "anthropic":
        app.config["ANTHROPIC_API_KEY"] = lc.get("api_key")

def sync_runtime_state(app: Flask, cfg: dict, changed_keys: set[str] | None = None) -> None:
    changed = set(changed_keys or cfg.keys())

    for key in changed:
        if key in cfg and hasattr(settings, key):
            try:
                setattr(settings, key, cfg.get(key))
                if key.upper() in app.config:
                    app.config[key.upper()] = cfg.get(key)
            except Exception as e:
                app.logger.warning(f"Konnte settings.{key} nicht aktualisieren: {e}")

    provider_url_fields = {
        "ollama_url": "ollama",
        "lmstudio_url": "lmstudio",
        "openai_url": "openai",
        "anthropic_url": "anthropic",
    }
    provider_urls = dict(app.config.get("PROVIDER_URLS", {}) or {})
    provider_urls_changed = False
    for field_name, provider_name in provider_url_fields.items():
        if field_name not in changed or field_name not in cfg:
            continue
        provider_urls[provider_name] = cfg.get(field_name)
        provider_urls_changed = True
        if provider_name == "openai":
            provider_urls["codex"] = cfg.get(field_name)

    if provider_urls_changed:
        app.config["PROVIDER_URLS"] = provider_urls

    if "openai_api_key" in changed and "openai_api_key" in cfg:
        app.config["OPENAI_API_KEY"] = cfg.get("openai_api_key")
    if "anthropic_api_key" in changed and "anthropic_api_key" in cfg:
        app.config["ANTHROPIC_API_KEY"] = cfg.get("anthropic_api_key")

    if "llm_config" in changed and "llm_config" in cfg:
        _sync_llm_config(app, cfg)

def _sync_llm_config(app: Flask, default_cfg: dict) -> None:
    lc = default_cfg.get("llm_config")
    if not lc:
        return
    prov = _sync_default_provider_settings(lc)
    if not prov:
        return
    _sync_provider_connection_settings(app, prov, lc)
