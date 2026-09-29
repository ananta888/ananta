"""Default worker execution blocks of the agent configuration.

Each function returns a fresh dict for one top-level key of
``agent.config_defaults.build_default_agent_config``.
"""

import os

from agent.config import settings


def worker_runtime_defaults() -> dict:
    return {
        "workspace_root": os.environ.get("ANANTA_WORKSPACE_ROOT") or None,
        "workspace_reuse_mode": "goal_worker",
        "preflight_scope": "worker",
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
            "sira": {
                "schema": "codecompass.sira-config.v1",
                "mode": (
                    str(settings.codecompass_sira_mode or "off").strip().lower()
                    if settings.codecompass_sira_online_expansion_enabled
                    else "off"
                ),
                "enrichment_model": str(settings.codecompass_sira_enrichment_model or "").strip(),
                "query_model": str(settings.codecompass_sira_query_model or "").strip(),
                "rerank_model": str(settings.codecompass_sira_rerank_model or "").strip(),
                "reranker_enabled": bool(settings.codecompass_sira_reranker_enabled),
                "local_models_only": bool(settings.codecompass_sira_local_models_only),
                "temperature": 0.0,
                "profile_version": "corpus-discriminative-lexical.v1",
            },
            "codecompass_vector": bool(settings.codecompass_vector_enabled),
            "codecompass_graph": bool(settings.codecompass_graph_enabled),
            "codecompass_relation_expansion": bool(settings.codecompass_relation_expansion_enabled),
            "codecompass_vector_retrieval": {
                "enabled": bool(settings.codecompass_vector_enabled),
                "index_path": settings.codecompass_vector_index_path,
                "embedding_records_path": settings.codecompass_vector_embedding_records_path,
                "manifest_path": settings.codecompass_vector_manifest_path,
                "embedding_text_profile": settings.codecompass_vector_embedding_text_profile,
                "fail_mode": settings.codecompass_vector_fail_mode,
            },
        },
        "native_worker_runtime": {
            "enabled": True,
            "fallback_backend": "sgpt",
        },
        "codecompass_auto_bundle": {
            "enabled": False,
            "task_kinds": [],
        },
    }


def ananta_worker_tool_loop_defaults() -> dict:
    return {
        "enabled": False,
        "schema": "ananta_worker_tool_loop.v1",
        "max_iterations": 6,
        "max_tool_calls": 12,
        "max_tool_result_chars": 8000,
        "max_invalid_outputs": 2,
        "allowed_tools": [
            "repo.list_files",
            "repo.read_file_range",
            "repo.grep",
            # WCRB-012: codecompass.retrieve / resolve_context are answered by codecompass.search (aliases)
            "codecompass.plan_context",
            "codecompass.search",
            "codecompass.architecture_overview",
            "codecompass.architecture_expand",
            "codecompass.architecture_diagram",
            "codecompass.search_symbols",
            "codecompass.expand_graph",
            "codecompass.get_file_context",
            "codecompass.get_domain_map",
            "codecompass.architecture_query",
            "git.status",
            "git.diff_readonly",
            "workspace.diff",
            "repo.apply_patch",
            "repo.write_file",
            "test.discover",
            "test.run",
        ],
        # TTR-010: candidate-only fast path; disabled preserves old behavior.
        "tiny_router": {
            "mode": "disabled",
            "kill_switch": False,
            "profile_order": [],
            "top_k": 5,
            "max_hops": 2,
            "max_total_ms": 1500,
            "max_prompt_chars": 4000,
            "min_confidence": None,
            "allowed_risk_classes": ["read"],
            "commercial_use": True,
            "allow_research_only": False,
        },
    }


def hub_direct_execution_defaults() -> dict:
    return {
        "enabled": False,
        "direct_before_worker": True,
        "fallback_to_worker": True,
        "require_policy_gate": True,
        "audit_enabled": True,
        "confidence_threshold": 0.8,
        "max_result_chars": 8000,
        "allowed_tools": [
            "repo.list_files",
            "repo.read_file_range",
            "repo.grep",
            "git.status",
            "git.diff_readonly",
            "test.discover",
            "workspace.diff",
        ],
    }


def ananta_worker_workspace_mutation_defaults() -> dict:
    return {
        "enabled": False,
        "mutation_mode": "read_only",
        "mode_by_task_kind": {
            "coding": "strict_patch_request",
            "bugfix": "strict_patch_request",
            "refactor": "strict_patch_request",
            "test": "strict_patch_request",
            "doc": "controlled_workspace",
            "analysis": "read_only",
            "review": "read_only",
            "research": "read_only",
            "plan_only": "read_only",
        },
        "escalate_to_strict_risks": ["high", "critical"],
        "strict_path_markers": [
            "auth",
            "oidc",
            "keycloak",
            "deployment",
            "kubernetes",
            "secret",
            "security",
            ".github",
            "docker-compose",
        ],
        "require_materialized_scope": True,
        "allowed_new_file_globs": [],
        "max_feedback_iterations": 4,
        "max_patch_attempts_per_file": 3,
        "max_invalid_outputs": 2,
        "max_diff_chars": 12000,
        "max_write_file_bytes": 262144,
        "max_replace_existing_bytes": 65536,
        "max_replace_range_lines": 120,
        "allowlisted_test_commands": [],
        "test_timeout_seconds": 120,
        "test_output_max_chars": 4000,
    }


def worker_parallelism_defaults() -> dict:
    return {
        "schema": "ananta_worker_parallelism_config_v1",
        "enabled": True,
        "resource_caps_are_authoritative": True,
        "effective_concurrency_rule": (
            "min(security_policy_cap, worker_capacity, runtime_capacity, ollama_model_capacity)"
        ),
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
    }
