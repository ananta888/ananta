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
    runtime_profile = llm_configuration.get("runtime_profile") or {}
    assert runtime_profile.get("effective") in {"local-dev", "trusted-lab", "compose-safe", "distributed-strict"}
    assert runtime_profile.get("validation", {}).get("status") in {"ok", "error"}
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


def test_assistant_read_model_exposes_governance_risk_policy(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.get("/assistant/read-model", headers=headers)
    assert res.status_code == 200
    summary = (((res.json.get("data") or {}).get("settings") or {}).get("summary") or {})
    governance = summary.get("governance") or {}
    review_policy = governance.get("review_policy") or {}
    risk_policy = governance.get("execution_risk_policy") or {}
    assert review_policy.get("enabled") is True
    assert review_policy.get("min_risk_level_for_review") in {"high", "medium", "critical", "low"}
    assert risk_policy.get("enabled") is True
    assert risk_policy.get("default_action") in {"deny", "allow"}


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
