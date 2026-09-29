import os

from agent.config import settings
from agent.config_defaults_env import (
    apply_env_config_overrides as apply_env_config_overrides,
)
from agent.config_defaults_env import (
    merge_db_config_overrides as merge_db_config_overrides,
)
from agent.config_defaults_env import (
    sync_runtime_state as sync_runtime_state,
)
from agent.config_defaults_planning import planning_policy_defaults
from agent.config_defaults_quality import (
    approval_lifecycle_defaults,
    generated_source_line_policy_defaults,
)
from agent.config_defaults_security import (
    autopilot_security_policies_defaults,
    execution_risk_policy_defaults,
    exposure_policy_defaults,
    llm_tool_guardrails_defaults,
    shell_command_policy_defaults,
    terminal_policy_defaults,
)
from agent.config_defaults_worker import (
    ananta_worker_tool_loop_defaults,
    ananta_worker_workspace_mutation_defaults,
    hub_direct_execution_defaults,
    worker_parallelism_defaults,
    worker_runtime_defaults,
)
from agent.model_selection import normalize_legacy_model_name
from agent.runtime_profiles import runtime_profile_catalog


def _default_opencode_model() -> str | None:
    configured = str(getattr(settings, "opencode_default_model", None) or "").strip()
    if configured and configured != "opencode/glm-5-free":
        return normalize_legacy_model_name(configured, provider=str(settings.default_provider or "").strip().lower())
    if str(settings.default_provider or "").strip().lower() == "ollama":
        return "qwen2.5-coder:7b"
    return configured or None


def _default_decision_providers() -> dict:
    import copy

    from agent.services.decision_providers.config import DEFAULTS

    return copy.deepcopy(DEFAULTS)


def _default_context_strategy() -> dict:
    from agent.services.context_strategy_service import DEFAULTS

    return dict(DEFAULTS)


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
            "context_limit": settings.default_context_tokens,
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
        "llm_tool_guardrails": llm_tool_guardrails_defaults(),
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.2,
            "retry_max_backoff_seconds": 5.0,
            "retry_jitter_factor": 0.2,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
        "autopilot": {
            "async_dispatch_enabled": False,
        },
        "autopilot_worker_policy": {
            "enabled": False,
            "enforce_required_capabilities": True,
            "enforce_llm_scope": True,
            "worker_restart_grace_seconds": 120,
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
            "worker_404_hub_fallback_enabled": True,
            "worker_task_sync_from_hub_enabled": True,
        },
        "mutation_gate": {
            "enabled": True,
            "global_deny_mutations": False,
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
        "exposure_policy": exposure_policy_defaults(),
        "platform_mode": "local-dev",
        "terminal_policy": terminal_policy_defaults(),
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
        "execution_risk_policy": execution_risk_policy_defaults(),
        "shell_command_policy": shell_command_policy_defaults(),
        "autopilot_security_policies": autopilot_security_policies_defaults(),
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "ananta-worker",
            "coding_agent_free_first": True,
            "allow_paid_coding_agent_fallback": False,
            "coding_agent_permission_mode": "workspace_write",
            "backend_parallel_limits": {
                "sgpt": 1,
                "ananta-worker": 1,
                "codex": 1,
                "opencode": 1,
                "aider": 1,
                "mistral_code": 1,
                "qwen_code": 1,
                "gemini_cli": 1,
                "copilot_cli": 1,
                "cline": 1,
                "kilo_code": 1,
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
            # CCA-002: codex auth mode. "api_key" is the legacy
            # behaviour; "chatgpt_login" lets codex CLI use its
            # own ~/.codex/auth.json without OPENAI_API_KEY.
            "auth_mode": "api_key",
            # When auth_mode=chatgpt_login, this is ignored.
            "api_key_required": True,
        },
        # CLA-001: Claude Code / Claude CLI worker-agent config.
        # Enabled defaults to false so we never auto-activate a
        # backend that may not be installed. The user opts in via
        # CLAUDE_CODE_ENABLED=1 in .env or via the POST /config
        # endpoint.
        "claude_cli": {
            "enabled": False,
            "command": "claude",
            "auth_mode": "claude_login",
            "default_model": "claude-code-default",
            "permission_mode": "plan",
            "timeout_seconds": 1800,
            "max_concurrent_runs": 1,
            "allowed_paths": [],
            "write_armed_default": False,
        },
        # CTA-014/CTA-001: classroom transcript assistant. Opt-in;
        # room_mappings/schedule sind Context-Hint-Quellen, keine
        # Antwortquellen. webhook_secrets pro source (HMAC sha256).
        "classroom": {
            "enabled": False,
            "webhook_secrets": {},
            "room_mappings": {},
            "schedule": [],
            "question_confidence_threshold": 0.6,
            "retention_hours_raw_segments": 72,
            "n8n_examples_dir": "rag-helper/tests/fixtures/n8n",
            "teaching_index_file": "",
            "max_context_tokens": 2000,
        },
        "text_quality": {
            "enabled": False,
            "evaluate_planning_outputs": False,
            "llm_judge_enabled": False,
            "rewrite_enabled": False,
            "default_profile": "critical_editor_de",
            "max_input_chars": 12000,
            "max_input_words": 2500,
            "max_output_bytes": 262144,
            "max_slop_score": 0.35,
            "min_depth_score": 0.7,
            "min_canary_runs": 10,
            "external_detectors": {
                "avoid_ai_writing": {
                    "enabled": False,
                    "execution_plane": "sandbox",
                    "network": "none",
                    "pinned_commit": "f9f8265061eaee1004e9ef86383959163dd2477d",
                    "detector_sha256": "66cc34590ed17d4b7d7c59323b204e2b231e41ed58502ab89fc9753a64c278f5",
                    "context_mode": "technical",
                }
            },
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
            "execution_mode": (os.environ.get("ANANTA_OPENCODE_EXECUTION_MODE") or "live_terminal").strip().lower()
            or "live_terminal",
            "interactive_launch_mode": (os.environ.get("ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE") or "run")
            .strip()
            .lower()
            or "run",
            "target_provider": (
                str(settings.default_provider or "").strip().lower()
                if str(settings.default_provider or "").strip().lower() in {"ollama", "lmstudio"}
                else None
            ),
        },
        "worker_runtime": worker_runtime_defaults(),
        # AWTCL-004: hub-controlled tool calling loop for ananta-worker.
        # Disabled by default; the existing context batch loop stays the fallback.
        "knowledge_hygiene": {
            "enabled": False,
            "mode": "disabled",
            "auto_run_enabled": False,
            "source_writeback_enabled": False,
            "require_dual_approval": False,
            "max_claims_per_run": 10000,
            "max_candidate_pairs": 50000,
            "max_pages_per_run": 500,
            "max_patch_bytes": 1000000,
            "semantic_similarity_threshold": 0.92,
            "projection_dir": "artifacts/domain/knowledge-hygiene/wiki",
            "allowed_obsidian_roots": [],
        },
        "ananta_worker_tool_loop": ananta_worker_tool_loop_defaults(),
        # HDE-002: hub-direct execution before worker/LLM. Disabled by
        # default. The hub only decides, authorizes and audits (control
        # plane, HDW-DD-001); tool execution is dispatched to a
        # WorkerRuntime (execution plane, HDW-002) — never run as
        # untrusted logic inside the hub process.
        "hub_direct_execution": hub_direct_execution_defaults(),
        # DPRV: typed decision providers (TypeSafe Jev, local /v1/decision, LLM) per decision area.
        # Off by default; see docs/decision-providers.md and agent/services/decision_providers/config.py.
        "decision_providers": _default_decision_providers(),
        # LCTX-004: Hub decision for tasks whose context exceeds the window (docs/long-context-strategy.md)
        "context_strategy": _default_context_strategy(),
        # runtime context window (settings UI): {"profile": "full_64k"} or {"profile": "custom", "tokens": N};
        # empty: ANANTA_CONTEXT_PROFILE / ANANTA_CONTEXT_TOKENS (agent/context_profile.py)
        "context_window": {},
        # prompt limits of subscription/cloud model families (substring of the model name -> tokens), overriding
        # the defaults in agent/context_profile.py, e.g. {"claude": 1000000} for a 1M-context subscription
        "cloud_model_limits": {},
        # AWWPI-013: workspace mutation loop for ananta-worker. Disabled by
        # default; mutation_mode defaults to read_only and can be derived per
        # task_kind. Risk rules escalate controlled_workspace to
        # strict_patch_request.
        "ananta_worker_workspace_mutation": ananta_worker_workspace_mutation_defaults(),
        "generated_source_line_policy": generated_source_line_policy_defaults(),
        # ALWA-011/020: persistent approval lifecycle. Disabled by default —
        # legacy approval_confirmed keeps working as explicit
        # backward-compatibility policy until the lifecycle is rolled out.
        # Auto-approval never grants the human_required tools.
        "approval_lifecycle": approval_lifecycle_defaults(),
        "propose_policy": {
            "context_compaction_enabled": True,
            "context_compaction_required": False,
            "context_compactor_fail_open": False,
            "context_compactor_profile": "default",
            "context_compactor_timeout_seconds": 45,
            "context_compactor_max_output_chars": 12000,
            "context_compactor_retry_attempts": 1,
            "context_compactor_preserve_keywords": [
                "security",
                "policy",
                "verification",
                "review",
                "constraints",
            ],
        },
        # OHA-003: OpenHuman-inspired feature flags — all off/safe by default.
        "memory_tree": {
            "enabled": False,
            "mode": "safe_readonly",
            "auto_ingest_knowledge_index": False,
            "auto_ingest_result_memory": False,
            "llm_summary_enabled": False,
            "llm_summary_cloud_allowed": False,
            "max_leaves_per_source": 500,
            "seal_threshold_leaves": 20,
        },
        "tool_output_compaction": {
            "enabled": True,
            "fail_open": True,
            "builtin_rules_enabled": True,
            "project_rules_path": ".ananta/tool-output-rules",
            "max_input_chars_for_compaction": 4000,
            "max_output_chars": 2000,
            "always_preserve_signals": True,
        },
        "hint_routing": {
            "enabled": False,
            "mode": "compatibility",
            "cloud_allowed_hints": [],
            "local_only_hints": [
                "hint:context_compaction",
                "hint:cheap_classify",
                "hint:local_embedding",
            ],
            "unknown_hint_action": "mark_unavailable",
        },
        "local_ai": {
            "runtime_enabled": False,
            "usage": {
                "embeddings": False,
                "summary_tree": False,
                "context_compaction": True,
                "format_normalization": True,
                "cheap_classify": True,
            },
            "health_gate": {
                "require_probe_ok": True,
                "max_probe_age_seconds": 300,
            },
            "hardware_profiles": {
                "rtx3080": {"summary_tree": True, "context_compaction": True, "embeddings": True},
                "laptop": {"summary_tree": False, "context_compaction": True, "embeddings": False},
                "cpu_only": {"summary_tree": False, "context_compaction": True, "embeddings": False},
            },
        },
        "memory_vault_export": {
            "enabled": False,
            "output_dir": ".ananta/memory",
            "export_mode": "local_only",
            "exclude_sensitivity": ["secret", "credential", "security_sensitive"],
        },
        "memory_tree_autofetch": {
            "enabled": False,
            "internal_only": True,
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
        "worker_parallelism": worker_parallelism_defaults(),
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
        "planning_policy": planning_policy_defaults(),
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
