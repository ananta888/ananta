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


def test_set_config_rejects_invalid_runtime_profile(client, admin_token):
    response = client.post(
        "/config",
        json={"runtime_profile": "invalid-profile"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json["message"] == "invalid_runtime_profile"


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
    assert cfg["context_bundle_policy"] == {
        "mode": "compact",
        "compact_max_chunks": 1,
        "standard_max_chunks": 12,
    }


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
    research_backend = llm_configuration.get("research_backend") or {}
    assert research_backend.get("provider") == "deerflow"
    assert research_backend.get("enabled") is True
    assert "providers" in research_backend
    assert (research_backend.get("review_policy") or {}).get("required") is True


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


def test_assistant_read_model_exposes_governance_risk_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/assistant/read-model", headers=headers)
    assert res.status_code == 200
    summary = (((res.json.get("data") or {}).get("settings") or {}).get("summary") or {})
    llm = summary.get("llm") or {}
    assert "artifact_flow" in llm
    governance = summary.get("governance") or {}
    review_policy = governance.get("review_policy") or {}
    risk_policy = governance.get("execution_risk_policy") or {}
    assert review_policy.get("enabled") is True
    assert review_policy.get("min_risk_level_for_review") in {"high", "medium", "critical", "low"}
    assert risk_policy.get("enabled") is True
    assert risk_policy.get("default_action") in {"deny", "allow"}
    exposure_policy = governance.get("exposure_policy") or {}
    openai_compat = exposure_policy.get("openai_compat") or {}
    assert openai_compat.get("enabled") in {True, False}
    assert openai_compat.get("require_admin_for_user_auth") in {True, False}


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
    assert mock_generate.call_args.kwargs["provider"] == "openai"
    assert mock_generate.call_args.kwargs["base_url"] == "http://127.0.0.1:8010/v1"
