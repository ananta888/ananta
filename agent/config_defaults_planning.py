"""Default goal-planning policy block of the agent configuration.

Returns a fresh dict for the ``planning_policy`` key of
``agent.config_defaults.build_default_agent_config``.
"""


def planning_policy_defaults() -> dict:
    return {
        "delegated_planning_enabled": False,
        "allowed_planner_roles": ["planning-agent", "planner"],
        "require_review": True,
        "allow_remote_planners": False,
        "max_nodes": 8,
        "max_depth": 8,
        "timeout_seconds": 600,
        # Separate slot-queue wait from planner execution time (PRI-005).
        # queue_wait_timeout_seconds: max time a goal waits to acquire a planning slot.
        # timeout_seconds: max time the actual planner LLM call may run once the slot is held.
        # Default: queue wait equals planner timeout for backward compatibility.
        "queue_wait_timeout_seconds": None,
        # Number of goals that may execute planning concurrently.
        # Keep default serial for backward compatibility.
        "parallel_goal_planning_max_concurrency": 1,
        "max_output_tokens": 900,
        "segmented_planning_enabled": True,
        # LCTX: ~2k tokens per segment within the 32k window (was 2400 chars ~ 600 tokens);
        # segments grow with the context up to 8 instead of cutting (planning_strategies)
        "segment_context_chars": 8000,
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
            "auto_enable": False,
            "max_prompt_chars": 12000,
            "max_auto_evolutions_per_window": 3,
            "review_window_seconds": 3600,
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
    }
