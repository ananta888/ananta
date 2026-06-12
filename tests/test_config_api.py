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


def test_get_config(client, admin_token):
    response = client.get("/config", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert "data" in response.json
    assert "runtime_profile_effective" in response.json["data"]
    assert ((response.json["data"].get("opencode_runtime") or {}).get("execution_mode")) == "live_terminal"
    assert response.json["data"]["runtime_profile_effective"]["effective"] in {
        "local-dev",
        "trusted-lab",
        "compose-safe",
        "distributed-strict",
    }


def test_get_config_exposes_effective_template_variable_registry(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    response = client.get("/config", headers=headers)

    assert response.status_code == 200
    data = response.json["data"]
    assert "template_variables_allowlist" in data
    assert "template_variable_registry" in data
    assert data["template_variable_registry"]["allowed_names"] == data["template_variables_allowlist"]
    assert "task" in (data["template_variable_registry"].get("supported_context_scopes") or [])
    assert "team_goal" in data["template_variables_allowlist"]
    assert "goal_context" in data["template_variables_allowlist"]
    assert "acceptance_criteria" in data["template_variables_allowlist"]


def test_set_config_rejects_invalid_runtime_profile(client, admin_token):
    response = client.post(
        "/config",
        json={"runtime_profile": "invalid-profile"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json["message"] == "invalid_runtime_profile"


def test_set_config_accepts_actionable_runtime_profiles(client, admin_token):
    response = client.post(
        "/config",
        json={"runtime_profile": "review-first", "governance_mode": "strict"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    config_res = client.get("/config", headers={"Authorization": f"Bearer {admin_token}"})
    runtime = config_res.json["data"]["runtime_profile_effective"]
    assert runtime["effective"] == "review-first"
    assert runtime["profile"]["default_governance_mode"] == "strict"
    assert runtime["profile"]["usage_context"] == "production"


def test_set_config_rejects_invalid_execution_fallback_policy(client, admin_token):
    response = client.post(
        "/config",
        json={"execution_fallback_policy": {"fallback_block_status": "invalid"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json["message"] == "invalid_fallback_block_status"


def test_set_config_rejects_invalid_autonomous_retry_strategy(client, admin_token):
    response = client.post(
        "/config",
        json={"autonomous_resilience": {"retry_backoff_strategy": "zigzag"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json["message"] == "invalid_retry_backoff_strategy"


def test_set_config_unwrapping(client, admin_token):
    # Wir senden eine verschachtelte Konfiguration (simulierter Bug im Frontend/API)
    nested_config = {
        "llm_config": {"status": "success", "data": {"provider": "openai", "model": "gpt-4o"}},
        "default_provider": "openai",
    }

    response = client.post("/config", json=nested_config, headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200

    # Jetzt prüfen ob es korrekt in der DB und im app.config gelandet ist
    # Wir rufen GET /config auf
    get_res = client.get("/config", headers={"Authorization": f"Bearer {admin_token}"})
    config_data = get_res.json["data"]

    assert config_data["llm_config"]["provider"] == "openai"
    assert config_data["llm_config"]["model"] == "gpt-4o"
    assert config_data["default_provider"] == "openai"
    # Sicherstellen, dass "status" und "data" Schlüssel weg sind
    assert "status" not in config_data["llm_config"]


def test_set_config_forbidden_for_user(client):
    # Normalen User anlegen
    from werkzeug.security import generate_password_hash

    from agent.db_models import UserDB
    from agent.repository import user_repo

    username = "normal_user"
    password = "password123"
    user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role="user"))

    # Login
    login_res = client.post("/login", json={"username": username, "password": password})
    token = login_res.json["data"]["access_token"]

    # POST versuchen
    response = client.post("/config", json={"foo": "bar"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_llmstudio_mode_persists(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {"llm_config": {"provider": "lmstudio", "lmstudio_api_mode": "completions"}}
    response = client.post("/config", json=payload, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["llm_config"]["lmstudio_api_mode"] == "completions"


def test_llmstudio_mode_not_dropped_by_partial_llm_update(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {"llm_config": {"provider": "lmstudio", "model": "m1", "lmstudio_api_mode": "completions"}}
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    # Simulate frontend update payloads that omit mode while changing other fields.
    second = {"llm_config": {"provider": "lmstudio", "model": "m2"}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["llm_config"]["model"] == "m2"
    assert cfg["llm_config"]["lmstudio_api_mode"] == "completions"


def test_hub_copilot_config_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {
        "hub_copilot": {
            "enabled": True,
            "provider": "OPENAI",
            "model": "gpt-4o",
            "base_url": "https://example.invalid/v1/chat/completions",
            "temperature": 1.7,
            "strategy_mode": "planning_and_routing",
        }
    }
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    second = {"hub_copilot": {"model": "gpt-4.1-mini"}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["hub_copilot"]["enabled"] is True
    assert cfg["hub_copilot"]["provider"] == "openai"
    assert cfg["hub_copilot"]["model"] == "gpt-4.1-mini"
    assert cfg["hub_copilot"]["base_url"] == "https://example.invalid/v1/chat/completions"
    assert cfg["hub_copilot"]["strategy_mode"] == "planning_and_routing"
    assert cfg["hub_copilot"]["temperature"] == 1.7


def test_hub_copilot_invalid_mode_falls_back_to_planning_only(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(
        "/config",
        json={"hub_copilot": {"enabled": True, "strategy_mode": "invalid-mode"}},
        headers=headers,
    )
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    cfg = get_response.json["data"]
    assert cfg["hub_copilot"]["strategy_mode"] == "planning_only"


def test_context_bundle_policy_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {
        "context_bundle_policy": {
            "mode": "STANDARD",
            "compact_max_chunks": 0,
            "standard_max_chunks": 12,
        }
    }
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    second = {"context_bundle_policy": {"mode": "compact"}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    context_policy = cfg["context_bundle_policy"]
    assert context_policy["mode"] == "compact"
    assert context_policy["compact_max_chunks"] == 1
    assert context_policy["standard_max_chunks"] == 12
    assert context_policy["window_profile"] == "standard_32k"
    assert context_policy["standard_budget_tokens"] == 32000


def test_artifact_flow_config_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {
        "artifact_flow": {
            "enabled": True,
            "rag_enabled": True,
            "rag_top_k": 0,
            "rag_include_content": True,
            "max_tasks": 9999,
            "max_worker_jobs_per_task": -5,
        }
    }
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    second = {"artifact_flow": {"rag_enabled": False}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["artifact_flow"] == {
        "enabled": True,
        "rag_enabled": False,
        "rag_top_k": 1,
        "rag_include_content": True,
        "max_tasks": 200,
        "max_worker_jobs_per_task": 1,
    }


def test_doom_loop_policy_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {
        "doom_loop_policy": {
            "enabled": True,
            "lookback_signals": 999,
            "repeated_tool_call_threshold": 1,
            "repeated_failure_threshold": -3,
            "no_progress_threshold": 1,
            "oscillation_threshold": 1,
            "critical_abort_threshold": 3,
            "severity_actions": {"critical": "ABORT", "medium": "unknown"},
            "enforce_pause_abort": True,
        }
    }
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    second = {"doom_loop_policy": {"severity_actions": {"high": "pause"}}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["doom_loop_policy"] == {
        "enabled": True,
        "lookback_signals": 200,
        "repeated_tool_call_threshold": 2,
        "repeated_failure_threshold": 2,
        "no_progress_threshold": 2,
        "oscillation_threshold": 4,
        "critical_abort_threshold": 4,
        "severity_actions": {
            "low": "warn",
            "medium": "inject_correction",
            "high": "pause",
            "critical": "abort",
        },
        "enforce_pause_abort": True,
    }


def test_unified_approval_policy_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    first = {
        "unified_approval_policy": {
            "enabled": True,
            "enforce_confirm_required": True,
            "governance_overrides": {
                "balanced": {
                    "confirm_required": ["system_mutation", "install_remove", "invalid"],
                    "blocked": ["admin_mutation", "invalid"],
                }
            },
        }
    }
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    second = {"unified_approval_policy": {"governance_overrides": {"safe": {"confirm_required": ["mutation", "read_only"]}}}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    approval = cfg["unified_approval_policy"]
    assert approval["enabled"] is True
    assert approval["enforce_confirm_required"] is True
    safe_override = (approval.get("governance_overrides") or {}).get("safe") or {}
    balanced_override = (approval.get("governance_overrides") or {}).get("balanced") or {}
    assert safe_override["confirm_required"] == ["mutation", "read_only"]
    assert "admin_mutation" in (safe_override.get("blocked") or [])
    assert balanced_override["confirm_required"] == ["system_mutation", "install_remove"]
    assert balanced_override["blocked"] == ["admin_mutation"]


def test_model_override_maps_are_normalized(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(
        "/config",
        json={
            "role_model_overrides": {
                " Backend Developer ": " qwen2.5-coder-14b ",
                "": "ignored",
            },
            "template_model_overrides": {
                " Scrum Sprint ": " glm-4-9b-0414 ",
            },
            "task_kind_model_overrides": {
                " CODING ": " qwen2.5-coder-7b ",
                "analysis": "",
            },
        },
        headers=headers,
    )
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["role_model_overrides"] == {"backend developer": "qwen2.5-coder-14b"}
    assert cfg["template_model_overrides"] == {"scrum sprint": "glm-4-9b-0414"}
    assert cfg["task_kind_model_overrides"] == {"coding": "qwen2.5-coder-7b"}


def test_specialized_worker_profiles_config_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(
        "/config",
        json={
            "specialized_worker_profiles": {
                "enabled": True,
                "profiles": {
                    "ml_intern": {
                        "enabled": True,
                        "capability_classes": ["ml_research", "invalid", "research"],
                        "risk_class": "HIGH",
                        "requires_approval": True,
                        "routing_aliases": ["ml-intern", "ML-INTERN"],
                    }
                },
            }
        },
        headers=headers,
    )
    assert response.status_code == 200

    response = client.post(
        "/config",
        json={"specialized_worker_profiles": {"profiles": {"ml_intern": {"available": True}}}},
        headers=headers,
    )
    assert response.status_code == 200

    cfg = client.get("/config", headers=headers).json["data"]
    profile = cfg["specialized_worker_profiles"]["profiles"]["ml_intern"]
    assert cfg["specialized_worker_profiles"]["enabled"] is True
    assert profile["capability_classes"] == ["ml_research", "research"]
    assert profile["risk_class"] == "high"
    assert profile["available"] is True
    assert profile["routing_aliases"] == ["ml-intern"]


def test_ml_intern_spike_config_is_normalized_and_merged(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(
        "/config",
        json={
            "ml_intern_spike": {
                "enabled": True,
                "command_template": "python worker.py --prompt-file {prompt_file}",
                "timeout_seconds": 1,
                "max_prompt_chars": 999999,
                "max_output_chars": 10,
                "env_allowlist": ["HOME", "", "HOME"],
            }
        },
        headers=headers,
    )
    assert response.status_code == 200

    response = client.post("/config", json={"ml_intern_spike": {"working_dir": "agent"}}, headers=headers)
    assert response.status_code == 200

    cfg = client.get("/config", headers=headers).json["data"]["ml_intern_spike"]
    assert cfg["enabled"] is True
    assert cfg["timeout_seconds"] == 10
    assert cfg["max_prompt_chars"] == 64000
    assert cfg["max_output_chars"] == 512
    assert cfg["env_allowlist"] == ["HOME"]
    assert cfg["working_dir"] == "agent"


def test_model_override_maps_reject_non_objects(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(
        "/config",
        json={"role_model_overrides": ["invalid"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json["message"] == "invalid_role_model_overrides"


def test_set_config_updates_runtime_provider_urls_for_flat_keys(client, admin_token, app):
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "lmstudio_url": "http://127.0.0.1:1234/v1",
        "openai_url": "https://example.invalid/openai/v1/chat/completions",
        "anthropic_url": "https://example.invalid/anthropic/v1/messages",
        "openai_api_key": "sk-test-openai",
    }

    response = client.post("/config", json=payload, headers=headers)
    assert response.status_code == 200

    assert app.config["PROVIDER_URLS"]["lmstudio"] == "http://127.0.0.1:1234/v1"
    assert app.config["PROVIDER_URLS"]["openai"] == "https://example.invalid/openai/v1/chat/completions"
    assert app.config["PROVIDER_URLS"]["codex"] == "https://example.invalid/openai/v1/chat/completions"
    assert app.config["PROVIDER_URLS"]["anthropic"] == "https://example.invalid/anthropic/v1/messages"
    assert app.config["OPENAI_API_KEY"] == "sk-test-openai"


def test_list_providers_uses_dynamic_lmstudio_models(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-a"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [
            {"id": "model-a", "context_length": 8192},
            {"id": "model-b", "context_length": 4096},
        ]
        res = client.get("/providers", headers=headers)

    assert res.status_code == 200
    items = res.json["data"]
    ids = [i["id"] for i in items]
    assert "lmstudio:model-a" in ids
    assert "lmstudio:model-b" in ids
    selected = next((i for i in items if i["id"] == "lmstudio:model-a"), None)
    assert selected is not None and selected["selected"] is True


def test_provider_catalog_contains_dynamic_lmstudio_block(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-x"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [{"id": "model-x", "context_length": 32768}]
        res = client.get("/providers/catalog?force_refresh=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    assert data["default_provider"] == "lmstudio"
    lmstudio = next((p for p in data["providers"] if p["provider"] == "lmstudio"), None)
    assert lmstudio is not None
    assert lmstudio["available"] is True
    assert any(m["id"] == "model-x" and m["selected"] is True for m in (lmstudio.get("models") or []))


def test_provider_catalog_contains_codex_provider(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "codex", "default_model": "gpt-5-codex"},
        headers=headers,
    )
    res = client.get("/providers/catalog?force_refresh=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    codex = next((p for p in data["providers"] if p["provider"] == "codex"), None)
    assert codex is not None
    assert codex["capabilities"]["requires_api_key"] is True
    assert any(m["id"] == "gpt-5-codex" for m in (codex.get("models") or []))


def test_provider_catalog_includes_configured_local_openai_backend(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={
            "default_provider": "vllm_local",
            "default_model": "qwen2.5-coder",
            "local_openai_backends": [
                {
                    "id": "vllm_local",
                    "name": "vLLM Local",
                    "base_url": "http://127.0.0.1:8010/v1/chat/completions",
                    "models": ["qwen2.5-coder"],
                    "supports_tool_calls": True,
                }
            ],
        },
        headers=headers,
    )
    res = client.get("/providers/catalog?task_kind=coding", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    backend = next((p for p in data["providers"] if p["provider"] == "vllm_local"), None)
    assert backend is not None
    assert backend["base_url"] == "http://127.0.0.1:8010/v1"
    assert backend["capabilities"]["openai_compatible"] is True
    assert backend["capabilities"]["supports_tool_calls"] is True
    assert any(m["id"] == "qwen2.5-coder" and m["selected"] is True for m in (backend.get("models") or []))


def test_provider_catalog_handles_lmstudio_candidate_errors(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "fallback-model"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates", side_effect=RuntimeError("offline")):
        res = client.get("/providers/catalog?force_refresh=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    lmstudio = next((p for p in data["providers"] if p["provider"] == "lmstudio"), None)
    assert lmstudio is not None
    assert lmstudio["available"] is False
    assert lmstudio["model_count"] == 0
    assert lmstudio["models"] == []


def test_provider_catalog_uses_cache_and_force_refresh(client, admin_token):
    from agent.routes import config as config_routes

    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-cache"},
        headers=headers,
    )
    config_routes._LMSTUDIO_CATALOG_CACHE.clear()
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [{"id": "model-cache", "context_length": 8192}]

        r1 = client.get("/providers/catalog?cache_ttl_seconds=60", headers=headers)
        r2 = client.get("/providers/catalog?cache_ttl_seconds=60", headers=headers)
        r3 = client.get("/providers/catalog?cache_ttl_seconds=60&force_refresh=1", headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert mock_candidates.call_count == 2


def test_provider_catalog_passes_custom_lmstudio_timeout(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-timeout"},
        headers=headers,
    )
    seen = {"timeout": None}

    def _fake_candidates(_url, timeout=0):
        seen["timeout"] = timeout
        return [{"id": "model-timeout", "context_length": 4096}]

    with patch("agent.routes.config._list_lmstudio_candidates", side_effect=_fake_candidates):
        res = client.get("/providers/catalog?force_refresh=1&lmstudio_timeout_seconds=9", headers=headers)

    assert res.status_code == 200
    assert seen["timeout"] == 9


def test_provider_catalog_exposes_benchmark_recommendations_for_task_kind(client, admin_token, app, tmp_path):
    with app.app_context():
        app.config["DATA_DIR"] = str(tmp_path)
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-x"},
        headers=headers,
    )

    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-x",
            "task_kind": "coding",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 800,
            "tokens_total": 700,
        },
        headers=headers,
    )
    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "codex",
            "model": "gpt-5-codex",
            "task_kind": "coding",
            "success": False,
            "quality_gate_passed": False,
            "latency_ms": 2200,
            "tokens_total": 1400,
        },
        headers=headers,
    )

    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [{"id": "model-x", "context_length": 32768}]
        res = client.get("/providers/catalog?force_refresh=1&task_kind=coding", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    assert (data.get("recommendations") or {}).get("task_kind") == "coding"
    top = (data.get("recommendations") or {}).get("items") or []
    assert top and top[0]["id"] == "lmstudio:model-x"

    lmstudio = next((p for p in data["providers"] if p["provider"] == "lmstudio"), None)
    assert lmstudio is not None
    assert lmstudio["recommended_model"] == "model-x"
    model_entry = next((m for m in (lmstudio.get("models") or []) if m["id"] == "model-x"), None)
    assert model_entry is not None
    assert model_entry["recommended_rank"] == 1
    assert (model_entry.get("benchmark") or {}).get("suitability_score") is not None


def test_provider_catalog_omits_recommendations_without_task_kind(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/providers/catalog", headers=headers)
    assert res.status_code == 200
    assert "recommendations" not in (res.json.get("data") or {})


def test_dashboard_read_model_uses_benchmark_task_kind_rows(client, admin_token, app, tmp_path):
    with app.app_context():
        app.config["DATA_DIR"] = str(tmp_path)
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "research_backend": {
                "provider": "deerflow",
                "enabled": True,
                "mode": "cli",
                "command": "python main.py {prompt}",
                "working_dir": str(tmp_path),
            },
        }
    headers = {"Authorization": f"Bearer {admin_token}"}

    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "lmstudio",
            "model": "model-analysis",
            "task_kind": "analysis",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 500,
            "tokens_total": 600,
        },
        headers=headers,
    )
    client.post(
        "/llm/benchmarks/record",
        json={
            "provider": "codex",
            "model": "gpt-5-codex",
            "task_kind": "coding",
            "success": True,
            "quality_gate_passed": True,
            "latency_ms": 450,
            "tokens_total": 500,
        },
        headers=headers,
    )

    res = client.get("/dashboard/read-model?benchmark_task_kind=coding&include_task_snapshot=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    assert (data.get("system_health") or {}).get("checks") is not None
    assert (data.get("contracts") or {}).get("version") == "v1"
    assert ((data.get("contracts") or {}).get("task_statuses") or {}).get("canonical_values")
    assert ((data.get("contracts") or {}).get("task_state_machine") or {}).get("transitions")
    assert (data.get("benchmarks") or {}).get("task_kind") == "coding"
    items = (data.get("benchmarks") or {}).get("items") or []
    assert items and items[0]["id"] == "codex:gpt-5-codex"
    assert (items[0].get("focus") or {}).get("suitability_score") is not None
    recommendation = (data.get("benchmarks") or {}).get("recommendation") or {}
    assert (recommendation.get("recommended") or {}).get("selection_source") == "benchmarks_available_top_ranked"
    assert recommendation.get("is_recommendation_active") is False
    assert (data.get("tasks") or {}).get("included") is True
    llm_configuration = data.get("llm_configuration") or {}
    assert (llm_configuration.get("defaults") or {}).get("provider") is not None
    effective_runtime = llm_configuration.get("effective_runtime") or {}
    assert effective_runtime.get("benchmark_applied") is True
    assert effective_runtime.get("selection_source") == "benchmarks_available_top_ranked"
    assert "hub_copilot" in llm_configuration
    assert "context_bundle_policy" in llm_configuration
    assert "artifact_flow" in llm_configuration
    runtime_profile = llm_configuration.get("runtime_profile") or {}
    assert runtime_profile.get("effective") in {"local-dev", "trusted-lab", "compose-safe", "distributed-strict"}
    assert runtime_profile.get("validation", {}).get("status") in {"ok", "error"}
    cli_sessions = llm_configuration.get("cli_sessions") or {}
    assert (cli_sessions.get("policy") or {}).get("enabled") in {True, False}
    assert (cli_sessions.get("runtime") or {}).get("total") is not None
    routing_split = llm_configuration.get("routing_split") or {}
    assert (routing_split.get("inference") or {}).get("default_provider") is not None
    assert (routing_split.get("execution") or {}).get("default_backend") in {"sgpt", "codex", "opencode", "aider", "mistral_code", "auto"}
    assert (routing_split.get("decision_chain") or {}).get("policy_version") == "routing-decision-v1"
    assert (routing_split.get("fallback_policy") or {}).get("enabled") is True
    research_backend = llm_configuration.get("research_backend") or {}
    assert research_backend.get("provider") == "deerflow"
    assert research_backend.get("enabled") is True
    assert "providers" in research_backend
    assert (research_backend.get("review_policy") or {}).get("required") is True
    runtime_telemetry = llm_configuration.get("runtime_telemetry") or {}
    retrieval_bundles = runtime_telemetry.get("retrieval_bundles") or {}
    assert isinstance(retrieval_bundles.get("sample_size"), int)


def test_dashboard_read_model_can_skip_task_snapshot(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    res = client.get("/dashboard/read-model?benchmark_task_kind=analysis", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    tasks = data.get("tasks") or {}
    assert tasks.get("included") is False
    assert tasks.get("counts") == {"total": 0, "completed": 0, "failed": 0, "todo": 0, "in_progress": 0, "blocked": 0}
    assert tasks.get("recent") == []
    llm_configuration = data.get("llm_configuration") or {}
    runtime_telemetry = llm_configuration.get("runtime_telemetry") or {}
    assert isinstance(runtime_telemetry.get("providers"), dict)
    assert isinstance(runtime_telemetry.get("cli_backends"), dict)
    retrieval_bundles = runtime_telemetry.get("retrieval_bundles") or {}
    assert retrieval_bundles.get("sample_size") == 0
    assert isinstance(retrieval_bundles.get("by_task_kind"), dict)
    assert isinstance(retrieval_bundles.get("by_bundle_mode"), dict)
    critical_workflows = runtime_telemetry.get("critical_workflows") or {}
    assert critical_workflows.get("sample_size") == 0
    assert isinstance(critical_workflows.get("state_distribution"), list)
    assert isinstance(critical_workflows.get("rates"), dict)
