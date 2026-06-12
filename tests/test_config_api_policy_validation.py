from unittest.mock import patch

import pytest


@pytest.fixture
def admin_token(client):
    # Admin User anlegen
    from werkzeug.security import generate_password_hash

    from agent.db_models import UserDB
    from agent.repository import user_repo

    username = "api_test_admin"
    password = "password123"
    user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role="admin"))

    # Login
    response = client.post("/login", json={"username": username, "password": password})
    return response.json["data"]["access_token"]



# Split from tests/test_config_api.py to keep source files below 1000 lines.

def test_dashboard_read_model_exposes_operations_observability(client, admin_token):
    from agent.db_models import ContextBundleDB, EvolutionProposalDB, PolicyDecisionDB, TaskDB, VerificationRecordDB
    from agent.repository import context_bundle_repo, evolution_proposal_repo, policy_decision_repo, task_repo, verification_record_repo
    from agent.services.product_event_service import record_product_event

    task_repo.save(
        TaskDB(
            id="obs-task-1",
            title="Observe routing",
            status="failed",
            task_kind="coding",
            status_reason_code="verification_failed",
            context_bundle_id="obs-bundle-1",
        )
    )
    context_bundle_repo.save(
        ContextBundleDB(
            id="obs-bundle-1",
            task_id="obs-task-1",
            chunks=[{"source_type": "memory"}, {"source_type": "knowledge"}],
            token_estimate=1200,
            bundle_metadata={
                "budget": {"retrieval_utilization": 0.75},
                "strategy": {"source_mix": {"memory": 1, "knowledge": 1}},
            },
        )
    )
    policy_decision_repo.save(
        PolicyDecisionDB(
            task_id="obs-task-1",
            decision_type="routing",
            status="blocked",
            policy_name="routing-policy",
            policy_version="v1",
            reasons=["provider_unavailable"],
        )
    )
    verification_record_repo.save(
        VerificationRecordDB(
            task_id="obs-task-1",
            verification_type="quality_gate",
            status="failed",
            escalation_code="missing_test_evidence",
        )
    )
    evolution_proposal_repo.save(
        EvolutionProposalDB(
            id="obs-evo-prop-1",
            run_id="obs-evo-run-1",
            provider_name="api-evolution",
            task_id="obs-task-1",
            title="Observe blocked workflow",
            description="Proposal for observability",
            proposal_metadata={
                "workflow_state": {
                    "schema": "critical_workflow_state.v1",
                    "workflow_type": "evolution_proposal",
                    "state": "blocked",
                    "started_at": 100.0,
                    "last_transition_at": 103.0,
                    "transition_count": 3,
                    "recovery_attempts": 1,
                    "timeout_seconds": 300,
                    "max_recovery_attempts": 1,
                    "history": [
                        {"event_type": "workflow_transition", "from_state": "review_required", "to_state": "approved", "timestamp": 101.0},
                        {"event_type": "workflow_transition", "from_state": "approved", "to_state": "apply_requested", "timestamp": 102.0},
                        {"event_type": "workflow_transition", "from_state": "apply_requested", "to_state": "blocked", "timestamp": 103.0},
                    ],
                },
                "last_fallback": {
                    "reason": "apply_execution_fallback",
                    "cause": "mutation_gate_blocked:mutation_scope_mismatch",
                    "timestamp": 104.0,
                },
            },
        )
    )
    record_product_event(
        "goal_blocked",
        details={"source": "ui", "mode": "generic", "reason": "policy_override_requires_admin"},
        goal_id="goal-obs-1",
        trace_id="trace-obs-1",
    )
    record_product_event(
        "review_required",
        details={"source": "cli", "mode": "runtime_repair", "reason": "high_risk_action", "usage_context": "trial"},
        goal_id="goal-obs-2",
        trace_id="trace-obs-2",
    )
    record_product_event(
        "goal_planning_succeeded",
        details={"source": "api", "mode": "generic", "usage_context": "production"},
        goal_id="goal-obs-3",
        trace_id="trace-obs-3",
    )

    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/dashboard/read-model?include_task_snapshot=1", headers=headers)

    assert res.status_code == 200
    runtime_telemetry = (((res.json["data"].get("llm_configuration") or {}).get("runtime_telemetry")) or {})
    operations = runtime_telemetry.get("operations") or {}
    assert operations.get("sample_size", 0) >= 1
    assert {"key": "missing_test_evidence", "count": 1} in (operations.get("root_causes") or {}).get("verification_failures", [])
    assert {"key": "provider_unavailable", "count": 1} in (operations.get("root_causes") or {}).get("routing_reasons", [])
    assert ((operations.get("context_efficiency") or {}).get("by_task_kind") or {}).get("coding", {}).get("avg_budget_utilization") == 0.75
    product_events = operations.get("product_events") or {}
    assert product_events.get("sample_size", 0) >= 3
    assert (product_events.get("friction") or {}).get("blocked", 0) >= 1
    assert ((product_events.get("channels") or {}).get("counts") or {}).get("ui", 0) >= 1
    assert ((product_events.get("usage_contexts") or {}).get("counts") or {}).get("production", 0) >= 1
    critical_workflows = runtime_telemetry.get("critical_workflows") or {}
    assert critical_workflows.get("sample_size", 0) >= 1
    assert critical_workflows.get("blocked_transition_count", 0) >= 1
    assert critical_workflows.get("unstable_pattern_count", 0) >= 1
    assert {"key": "blocked", "count": 1} in (critical_workflows.get("state_distribution") or [])


def test_config_read_models_expose_effective_policy_profile(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    config_res = client.get("/config", headers=headers)
    assert config_res.status_code == 200
    profile = config_res.json["data"].get("effective_policy_profile") or {}
    assert profile.get("version") == "v1"
    assert profile.get("controls", {}).get("review", {}).get("enabled") in {True, False}

    dashboard_res = client.get("/dashboard/read-model", headers=headers)
    assert dashboard_res.status_code == 200
    llm_configuration = dashboard_res.json["data"].get("llm_configuration") or {}
    dashboard_profile = llm_configuration.get("effective_policy_profile") or {}
    assert dashboard_profile.get("governance_mode", {}).get("effective") in {"safe", "balanced", "strict"}
    assert "summary" in dashboard_profile
    learning = llm_configuration.get("planning_learning") or {}
    assert learning.get("snapshot") is not None
    assert learning.get("overview") is not None
    assert "preferred_output_shape" in (learning.get("overview") or {})
    assert "preferred_output_format" in (learning.get("overview") or {})


def test_assistant_read_model_exposes_governance_risk_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/assistant/read-model", headers=headers)
    assert res.status_code == 200
    summary = (((res.json.get("data") or {}).get("settings") or {}).get("summary") or {})
    llm = summary.get("llm") or {}
    assert "artifact_flow" in llm
    propose_policy = llm.get("propose_policy") or {}
    assert propose_policy.get("context_compaction_enabled") in {True, False}
    assert propose_policy.get("context_compaction_required") in {True, False}
    assert propose_policy.get("context_compactor_fail_open") in {True, False}
    assert str(propose_policy.get("context_compactor_profile") or "")
    planning_learning = llm.get("planning_learning") or {}
    assert planning_learning.get("snapshot") is not None
    assert planning_learning.get("overview") is not None
    assert "preferred_output_shape" in (planning_learning.get("overview") or {})
    assert "preferred_output_format" in (planning_learning.get("overview") or {})
    governance = summary.get("governance") or {}
    review_policy = governance.get("review_policy") or {}
    risk_policy = governance.get("execution_risk_policy") or {}
    mutation_gate = governance.get("mutation_gate") or {}
    assert review_policy.get("enabled") is True
    assert review_policy.get("min_risk_level_for_review") in {"high", "medium", "critical", "low"}
    assert risk_policy.get("enabled") is True
    assert risk_policy.get("default_action") in {"deny", "allow"}
    assert mutation_gate.get("enabled") in {True, False}
    assert mutation_gate.get("global_deny_mutations") in {True, False}
    exposure_policy = governance.get("exposure_policy") or {}
    openai_compat = exposure_policy.get("openai_compat") or {}
    assert openai_compat.get("enabled") in {True, False}
    assert openai_compat.get("require_admin_for_user_auth") in {True, False}
    platform_governance = governance.get("platform_governance") or {}
    assert platform_governance.get("policy_version") == "platform-governance-v1"
    assert platform_governance.get("platform_mode") in {"local-dev", "trusted-internal", "admin-only", "semi-public"}
    assert (platform_governance.get("terminal_policy") or {}).get("enabled") in {True, False}


def test_propose_policy_compactor_kill_switch_visible_and_editable(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    patch = client.post(
        "/config",
        json={
            "propose_policy": {
                "context_compaction_enabled": False,
                "context_compaction_required": False,
                "context_compactor_fail_open": False,
                "context_compactor_profile": "default",
            }
        },
        headers=headers,
    )
    assert patch.status_code == 200

    assistant = client.get("/assistant/read-model", headers=headers)
    assert assistant.status_code == 200
    summary = (((assistant.json.get("data") or {}).get("settings") or {}).get("summary") or {})
    propose_policy = ((summary.get("llm") or {}).get("propose_policy") or {})
    assert propose_policy.get("context_compaction_enabled") is False

    inventory = (((assistant.json.get("data") or {}).get("settings") or {}).get("editable_inventory") or [])
    assert any(item.get("key") == "propose_policy" for item in inventory)

    dashboard = client.get("/dashboard/read-model", headers=headers)
    assert dashboard.status_code == 200
    llm_cfg = ((dashboard.json.get("data") or {}).get("llm_configuration") or {})
    dash_policy = llm_cfg.get("propose_policy") or {}
    assert dash_policy.get("context_compaction_enabled") is False


def test_governance_policy_read_model_is_machine_readable(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/governance/policy", headers=headers)

    assert res.status_code == 200
    data = res.json.get("data") or {}
    assert data.get("policy_version") == "platform-governance-v1"
    assert data.get("platform_mode") == "local-dev"
    assert isinstance(data.get("decisions"), dict)
    assert data["decisions"]["terminal_interactive"]["allowed"] is False
    assert "remote_hubs" in data.get("exposure_policy", {})
    voice = (data.get("exposure_policy") or {}).get("voice") or {}
    assert voice.get("enabled") in {True, False}
    assert voice.get("require_explicit_approval_for_goal") in {True, False}


def test_set_config_validates_platform_mode_and_terminal_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    invalid_mode = client.post("/config", json={"platform_mode": "internet"}, headers=headers)
    assert invalid_mode.status_code == 400
    assert invalid_mode.json["message"] == "invalid_platform_mode"

    invalid_terminal = client.post("/config", json={"terminal_policy": "open"}, headers=headers)
    assert invalid_terminal.status_code == 400
    assert invalid_terminal.json["message"] == "invalid_terminal_policy"

    invalid_roles = client.post("/config", json={"terminal_policy": {"allowed_roles": "admin"}}, headers=headers)
    assert invalid_roles.status_code == 400
    assert invalid_roles.json["message"] == "invalid_terminal_allowed_roles"

    invalid_remote_hubs = client.post(
        "/config",
        json={"exposure_policy": {"remote_hubs": {"max_hops": 0}}},
        headers=headers,
    )
    assert invalid_remote_hubs.status_code == 400
    assert invalid_remote_hubs.json["message"] == "invalid_remote_hubs_max_hops"

    ok = client.post(
        "/config",
        json={
            "platform_mode": "admin-only",
            "terminal_policy": {
                "enabled": True,
                "allow_read": True,
                "allow_interactive": False,
                "require_admin": True,
                "max_session_seconds": 120,
                "idle_timeout_seconds": 30,
                "input_preview_max_chars": 64,
                "allowed_roles": ["operator"],
                "allowed_cidrs": ["127.0.0.1/32"],
            },
        },
        headers=headers,
    )
    assert ok.status_code == 200

    policy = client.get("/governance/policy", headers=headers)
    data = policy.json.get("data") or {}
    assert data["platform_mode"] == "admin-only"
    assert data["terminal_policy"]["enabled"] is True
    assert data["terminal_policy"]["allow_read"] is True
    assert data["terminal_policy"]["max_session_seconds"] == 120
    assert data["terminal_policy"]["idle_timeout_seconds"] == 30
    assert data["terminal_policy"]["input_preview_max_chars"] == 64
    assert data["terminal_policy"]["allowed_roles"] == ["operator"]


def test_set_config_validates_auth_provider(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    invalid = client.post("/config", json={"auth_provider": "oidc"}, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json["message"] == "invalid_auth_provider"

    ok = client.post("/config", json={"auth_provider": "oidc_bff"}, headers=headers)
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    assert (cfg.json.get("data") or {}).get("auth_provider") == "oidc_bff"


def test_set_config_validates_planning_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    invalid = client.post("/config", json={"planning_policy": "on"}, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json["message"] == "invalid_planning_policy"

    ok = client.post(
        "/config",
        json={
            "planning_policy": {
                "delegated_planning_enabled": True,
                "allowed_planner_roles": ["planning-agent", "planner"],
                "require_review": True,
                "allow_remote_planners": False,
                "max_nodes": 9,
                "max_depth": 6,
                "timeout_seconds": 40,
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200
    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    planning_policy = (cfg.json.get("data") or {}).get("planning_policy") or {}
    assert planning_policy.get("delegated_planning_enabled") is True
    assert planning_policy.get("max_nodes") == 9
    assert planning_policy.get("max_depth") == 6


def test_set_config_validates_routing_fallback_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    bad = client.post("/config", json={"routing_fallback_policy": "invalid"}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_routing_fallback_policy"

    bad_order = client.post("/config", json={"routing_fallback_policy": {"fallback_order": "remote_hub"}}, headers=headers)
    assert bad_order.status_code == 400
    assert bad_order.json["message"] == "invalid_routing_fallback_order"

    ok = client.post(
        "/config",
        json={
            "routing_fallback_policy": {
                "allow_remote_hubs": False,
                "fallback_order": ["configured_default", "local_runtime_probe"],
                "unavailable_action": "skip",
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    policy = (cfg.json.get("data") or {}).get("routing_fallback_policy") or {}
    assert policy["allow_remote_hubs"] is False
    assert policy["fallback_order"] == ["configured_default", "local_runtime_probe"]
    assert policy["unavailable_action"] == "skip"


def test_set_config_validates_memory_and_remote_federation_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    bad_memory = client.post("/config", json={"result_memory_policy": "invalid"}, headers=headers)
    assert bad_memory.status_code == 400
    assert bad_memory.json["message"] == "invalid_result_memory_policy"

    bad_federation = client.post("/config", json={"remote_federation_policy": {"allowed_operations": "chat"}}, headers=headers)
    assert bad_federation.status_code == 400
    assert bad_federation.json["message"] == "invalid_remote_federation_operations"

    ok = client.post(
        "/config",
        json={
            "result_memory_policy": {
                "archive_raw_output": True,
                "retrieval_document_max_chars": 800,
            },
            "remote_federation_policy": {
                "default_trust_level": "trusted-internal",
                "allowed_operations": ["models", "chat", "artifact"],
                "allow_artifact_access": True,
                "max_hops": 4,
            },
        },
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    data = cfg.json.get("data") or {}
    assert data["result_memory_policy"]["archive_raw_output"] is True
    assert data["remote_federation_policy"]["default_trust_level"] == "trusted-internal"
    assert data["remote_federation_policy"]["allow_artifact_access"] is True


def test_set_config_validates_exposure_policy_shape(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    bad = client.post("/config", json={"exposure_policy": {"openai_compat": "invalid"}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_openai_compat_exposure_policy"

    ok = client.post(
        "/config",
        json={
            "exposure_policy": {
                "openai_compat": {
                    "enabled": True,
                    "allow_user_auth": True,
                    "require_admin_for_user_auth": True,
                    "allow_files_api": False,
                },
                "mcp": {"enabled": False},
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    openai_compat = (((cfg.json.get("data") or {}).get("exposure_policy") or {}).get("openai_compat") or {})
    assert openai_compat.get("allow_files_api") is False


def test_set_config_rejects_invalid_openai_compat_max_hops(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.post(
        "/config",
        json={"exposure_policy": {"openai_compat": {"max_hops": 0}}},
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json["message"] == "invalid_openai_compat_max_hops"


def test_set_config_validates_cli_session_mode_shape(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = client.post("/config", json={"cli_session_mode": {"max_turns_per_session": 0}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_cli_session_max_turns"

    ok = client.post(
        "/config",
        json={
            "cli_session_mode": {
                "enabled": True,
                "stateful_backends": ["opencode", "codex"],
                "max_turns_per_session": 12,
                "max_sessions": 150,
                "allow_task_scoped_auto_session": True,
                "reuse_scope": "role",
                "native_opencode_sessions": True,
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200
    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    mode = ((cfg.json.get("data") or {}).get("cli_session_mode") or {})
    assert mode.get("enabled") is True
    assert mode.get("max_turns_per_session") == 12
    assert mode.get("reuse_scope") == "role"
    assert mode.get("native_opencode_sessions") is True


def test_set_config_validates_opencode_execution_mode(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = client.post("/config", json={"opencode_runtime": {"execution_mode": "tty"}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_opencode_execution_mode"

    bad_launch_mode = client.post("/config", json={"opencode_runtime": {"interactive_launch_mode": "fullscreen"}}, headers=headers)
    assert bad_launch_mode.status_code == 400
    assert bad_launch_mode.json["message"] == "invalid_opencode_interactive_launch_mode"

    bad_provider = client.post("/config", json={"opencode_runtime": {"target_provider": "openai"}}, headers=headers)
    assert bad_provider.status_code == 400
    assert bad_provider.json["message"] == "invalid_opencode_target_provider"

    ok = client.post(
        "/config",
        json={"opencode_runtime": {"tool_mode": "readonly", "execution_mode": "interactive_terminal", "interactive_launch_mode": "tui", "target_provider": "ollama"}},
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    runtime_cfg = ((cfg.json.get("data") or {}).get("opencode_runtime") or {})
    assert runtime_cfg.get("tool_mode") == "readonly"
    assert runtime_cfg.get("execution_mode") == "interactive_terminal"
    assert runtime_cfg.get("interactive_launch_mode") == "tui"
    assert runtime_cfg.get("target_provider") == "ollama"


def test_set_config_validates_worker_runtime_workspace_reuse_mode(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = client.post("/config", json={"worker_runtime": {"workspace_reuse_mode": "agent"}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_worker_workspace_reuse_mode"

    ok = client.post(
        "/config",
        json={"worker_runtime": {"workspace_root": "/tmp/worker-runtime", "workspace_reuse_mode": "goal_worker"}},
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    worker_runtime = ((cfg.json.get("data") or {}).get("worker_runtime") or {})
    assert worker_runtime.get("workspace_root") == "/tmp/worker-runtime"
    assert worker_runtime.get("workspace_reuse_mode") == "goal_worker"


def test_set_config_accepts_worker_semantic_output_correction_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = client.post("/config", json={"worker_runtime": {"semantic_output_correction": "on"}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_worker_semantic_output_correction"

    ok = client.post(
        "/config",
        json={
            "worker_runtime": {
                "semantic_output_correction": {
                    "enabled": True,
                    "similarity_threshold": 0.87,
                    "min_margin": 0.01,
                    "lexical_weight": 0.5,
                    "embedding_provider": {"provider": "local", "dimensions": 16},
                    "fields": {
                        "risk_classification": {
                            "enabled": True,
                            "candidates": ["low", "medium", "high", "critical"],
                        }
                    },
                }
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    worker_runtime = ((cfg.json.get("data") or {}).get("worker_runtime") or {})
    semantic_cfg = dict(worker_runtime.get("semantic_output_correction") or {})
    assert semantic_cfg.get("enabled") is True
    assert semantic_cfg.get("similarity_threshold") == 0.87
    assert semantic_cfg.get("lexical_weight") == 0.5
    assert dict(semantic_cfg.get("embedding_provider") or {}).get("provider") == "local"


def test_set_config_accepts_worker_todo_contract_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = client.post("/config", json={"worker_runtime": {"todo_contract": "on"}}, headers=headers)
    assert bad.status_code == 400
    assert bad.json["message"] == "invalid_worker_todo_contract"

    ok = client.post(
        "/config",
        json={
            "worker_runtime": {
                "todo_contract": {
                    "enabled": True,
                    "planner_llm_enabled": True,
                    "planner_llm_timeout_seconds": 9,
                    "planner_llm_retry_attempts": 2,
                    "max_tasks": 5,
                    "max_steps": 40,
                    "enforce_artifacts": True,
                    "default_executor_kind": "opencode",
                    "execution_mode": "assistant_execute",
                }
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200

    cfg = client.get("/config", headers=headers)
    assert cfg.status_code == 200
    worker_runtime = ((cfg.json.get("data") or {}).get("worker_runtime") or {})
    todo_cfg = dict(worker_runtime.get("todo_contract") or {})
    assert todo_cfg.get("enabled") is True
    assert todo_cfg.get("planner_llm_enabled") is True
    assert todo_cfg.get("planner_llm_timeout_seconds") == 9
    assert todo_cfg.get("planner_llm_retry_attempts") == 2
    assert todo_cfg.get("default_executor_kind") == "opencode"


def test_provider_catalog_cache_has_bounded_size(client, admin_token):
    from agent.routes import config as config_routes

    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-bound"},
        headers=headers,
    )

    config_routes._LMSTUDIO_CATALOG_CACHE.clear()
    with patch("agent.routes.config._list_lmstudio_candidates", return_value=[]):
        for i in range(config_routes._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES + 12):
            client.post(
                "/config",
                json={"lmstudio_url": f"http://127.0.0.1:{1200 + i}/v1"},
                headers=headers,
            )
            res = client.get("/providers/catalog?cache_ttl_seconds=60&force_refresh=1", headers=headers)
            assert res.status_code == 200

    assert len(config_routes._LMSTUDIO_CATALOG_CACHE) <= config_routes._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES


def test_provider_catalog_exposes_remote_ananta_backend_metadata(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={
            "default_provider": "ananta_remote_prod",
            "default_model": "gpt-4o",
            "remote_ananta_backends": [
                {
                    "id": "ananta_remote_prod",
                    "name": "Ananta Remote Prod",
                    "base_url": "https://ananta-remote.example/v1/chat/completions",
                    "models": ["gpt-4o", "gpt-5-codex"],
                    "instance_id": "remote-prod-1",
                    "max_hops": 5,
                }
            ],
        },
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates", return_value=[]):
        res = client.get("/providers/catalog?force_refresh=1", headers=headers)
    assert res.status_code == 200
    providers = (res.json.get("data") or {}).get("providers") or []
    remote = next((item for item in providers if item.get("provider") == "ananta_remote_prod"), None)
    assert remote is not None
    assert remote.get("available") is True
    caps = remote.get("capabilities") or {}
    assert caps.get("provider_type") == "remote_ananta"
    assert caps.get("remote_hub") is True
    assert (caps.get("remote_hub_policy") or {}).get("enabled") is True
    assert (remote.get("routing_decision") or {}).get("policy_version") == "routing-decision-v1"
    assert (caps.get("federation_policy") or {}).get("trust_level") == "partner"
    assert caps.get("allow_artifact_access") is False
    assert caps.get("instance_id") == "remote-prod-1"
    assert caps.get("max_hops") == 5


def test_llm_generate_uses_benchmark_recommendation_for_available_model(client, app, admin_token, tmp_path):
    from agent.routes import config as config_routes

    with app.app_context():
        app.config["DATA_DIR"] = str(tmp_path)
        app.config["AGENT_CONFIG"] = {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "llm_config": {},
            "local_openai_backends": [
                {
                    "id": "vllm_local",
                    "name": "vLLM Local",
                    "base_url": "http://127.0.0.1:8010/v1/chat/completions",
                    "models": ["qwen2.5-coder"],
                }
            ],
        }
        app.config["PROVIDER_URLS"] = {}
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "vllm_local",
            "model": "qwen2.5-coder",
            "task_kind": "coding",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 500,
            "tokens_total": 400,
        },
        headers=headers,
    )

    with patch.object(config_routes, "generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post("/llm/generate", json={"prompt": "fix failing test", "task_kind": "coding"}, headers=headers)

    assert res.status_code == 200
    payload = res.json["data"]
    routing = payload["routing"]
    assert routing["effective"]["provider"] == "vllm_local"
    assert routing["effective"]["transport_provider"] == "openai"
    assert routing["effective"]["model"] == "qwen2.5-coder"
    assert routing["recommendation"]["selection_source"] == "benchmarks_available_top_ranked"
    assert routing["decision_chain"]["steps"][0]["step"] == "task_benchmark"
    assert routing["fallback_policy"]["enabled"] is True
    assert (routing.get("tool_router") or {}).get("catalog_version") == "tool-router-v1"
    assert ((routing.get("tool_router") or {}).get("decision") or {}).get("policy_version") == "tool-router-v1"
    assert mock_generate.call_args.kwargs["provider"] == "openai"
    assert mock_generate.call_args.kwargs["base_url"] == "http://127.0.0.1:8010/v1"
