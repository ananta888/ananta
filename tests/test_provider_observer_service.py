from unittest.mock import patch


def test_provider_observer_probes_and_caches():
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    cfg = {"provider_observer_enabled": True, "provider_observer_ttl_seconds": 30, "provider_observer_timeout_seconds": 2}
    urls = {"ollama": "http://ollama:11434/api/generate", "lmstudio": "http://127.0.0.1:1234/v1"}

    with patch("agent.services.provider_observer_service.probe_ollama_runtime", return_value={"ok": True, "status": "ok", "candidate_count": 3}) as p_ollama, patch(
        "agent.services.provider_observer_service.probe_ollama_activity",
        return_value={"ok": True, "status": "ok", "active_count": 1},
    ), patch(
        "agent.services.provider_observer_service.probe_lmstudio_runtime",
        return_value={"ok": True, "status": "ok", "candidate_count": 2},
    ) as p_lm:
        snap1 = svc.snapshot(agent_config=cfg, provider_urls=urls)
        snap2 = svc.snapshot(agent_config=cfg, provider_urls=urls)

    assert snap1["enabled"] is True
    assert "ollama" in snap1["providers"]
    assert "lmstudio" in snap1["providers"]
    assert snap1["providers"]["ollama"]["source"] == "hub_direct_probe"
    assert snap1["providers"]["ollama"]["cache_hit"] is False
    assert snap2["providers"]["ollama"]["cache_hit"] is True
    assert p_ollama.call_count == 1
    assert p_lm.call_count == 1


def test_provider_observer_force_refresh_bypasses_cache():
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    cfg = {"provider_observer_enabled": True, "provider_observer_ttl_seconds": 30, "provider_observer_timeout_seconds": 2}
    urls = {"ollama": "http://ollama:11434/api/generate"}

    with patch("agent.services.provider_observer_service.probe_ollama_runtime", return_value={"ok": True, "status": "ok", "candidate_count": 1}) as p_ollama, patch(
        "agent.services.provider_observer_service.probe_ollama_activity",
        return_value={"ok": True, "status": "ok", "active_count": 0},
    ):
        svc.snapshot(agent_config=cfg, provider_urls=urls)
        svc.snapshot(agent_config=cfg, provider_urls=urls, force_refresh=True)

    assert p_ollama.call_count == 2


def test_autopilot_status_includes_provider_observer(client, admin_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = True
        app.config["AGENT_CONFIG"] = cfg
        app.config["PROVIDER_URLS"] = {
            "ollama": "http://ollama:11434/api/generate",
            "lmstudio": "http://127.0.0.1:1234/v1",
        }

    with patch(
        "agent.services.provider_observer_service.probe_ollama_runtime",
        return_value={"ok": True, "status": "ok", "candidate_count": 1},
    ), patch(
        "agent.services.provider_observer_service.probe_ollama_activity",
        return_value={"ok": True, "status": "ok", "active_count": 0},
    ), patch(
        "agent.services.provider_observer_service.probe_lmstudio_runtime",
        return_value={"ok": True, "status": "ok", "candidate_count": 1},
    ):
        res = client.get("/tasks/autopilot/status", headers=admin_auth_header)

    assert res.status_code == 200
    data = (res.json or {}).get("data") or {}
    observer = data.get("provider_observer") or {}
    assert observer.get("source") == "hub_direct_probe"
    assert "providers" in observer
    assert "ollama" in (observer.get("providers") or {})


def test_monitor_providers_live_forces_refresh(client, admin_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = True
        cfg["provider_observer_ttl_seconds"] = 60
        app.config["AGENT_CONFIG"] = cfg
        app.config["PROVIDER_URLS"] = {"ollama": "http://ollama:11434/api/generate"}

    with patch(
        "agent.services.provider_observer_service.probe_ollama_runtime",
        return_value={"ok": True, "status": "ok", "candidate_count": 1},
    ) as p_ollama, patch(
        "agent.services.provider_observer_service.probe_ollama_activity",
        return_value={"ok": True, "status": "ok", "active_count": 0},
    ):
        r1 = client.get("/api/system/monitor/providers/live", headers=admin_auth_header)
        r2 = client.get("/api/system/monitor/providers/live", headers=admin_auth_header)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert p_ollama.call_count == 2
    data = (r1.json or {}).get("data") or {}
    assert data.get("mode") == "live_force_refresh"


# PO-001: stable provider-observer endpoint
def test_provider_observer_endpoint_returns_snapshot(client, admin_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = True
        app.config["AGENT_CONFIG"] = cfg
        app.config["PROVIDER_URLS"] = {"ollama": "http://ollama:11434/api/generate"}

    with patch("agent.services.provider_observer_service.probe_ollama_runtime", return_value={"ok": True, "status": "ok"}), \
         patch("agent.services.provider_observer_service.probe_ollama_activity", return_value={"ok": True}):
        res = client.get("/api/system/provider-observer", headers=admin_auth_header)

    assert res.status_code == 200
    data = (res.json or {}).get("data") or {}
    assert "enabled" in data
    assert "providers" in data
    assert "observed_at" in data
    assert "ttl_seconds" in data


def test_provider_observer_endpoint_disabled_returns_enabled_false(client, admin_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = False
        app.config["AGENT_CONFIG"] = cfg
        app.config["PROVIDER_URLS"] = {"ollama": "http://ollama:11434/api/generate"}

    res = client.get("/api/system/provider-observer", headers=admin_auth_header)
    assert res.status_code == 200
    data = (res.json or {}).get("data") or {}
    assert data.get("enabled") is False


def test_provider_observer_force_refresh_blocked_for_non_admin(client, user_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = True
        app.config["AGENT_CONFIG"] = cfg
        app.config["PROVIDER_URLS"] = {"ollama": "http://ollama:11434/api/generate"}

    res = client.get("/api/system/provider-observer?force_refresh=true", headers=user_auth_header)
    assert res.status_code == 403
    assert res.json["message"] == "admin_required_for_force_refresh"


def test_provider_observer_force_refresh_allowed_for_admin(client, admin_auth_header, app):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["provider_observer_enabled"] = True
        cfg["provider_observer_ttl_seconds"] = 60
        app.config["AGENT_CONFIG"] = cfg
        # Use a distinct URL to avoid cross-test cache pollution from the singleton service
        app.config["PROVIDER_URLS"] = {"ollama": "http://ollama-unique-fr:11434/api/generate"}

    with patch("agent.services.provider_observer_service.probe_ollama_runtime", return_value={"ok": True, "status": "ok"}) as p_ollama, \
         patch("agent.services.provider_observer_service.probe_ollama_activity", return_value={"ok": True}):
        r1 = client.get("/api/system/provider-observer", headers=admin_auth_header)
        r2 = client.get("/api/system/provider-observer?force_refresh=true", headers=admin_auth_header)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # First call probes (cache miss for new URL), second call force-refreshes — both probe
    assert p_ollama.call_count == 2


# PO-002: probe errors never escape snapshot()
def test_provider_observer_probe_exception_does_not_propagate():
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    cfg = {"provider_observer_enabled": True, "provider_observer_ttl_seconds": 1}
    urls = {"ollama": "http://dead-host:11434/api/generate"}

    with patch("agent.services.provider_observer_service.probe_ollama_runtime", side_effect=ConnectionRefusedError("refused")), \
         patch("agent.services.provider_observer_service.probe_ollama_activity", side_effect=ConnectionRefusedError("refused")):
        snap = svc.snapshot(agent_config=cfg, provider_urls=urls)

    assert snap["enabled"] is True
    assert snap["providers"]["ollama"]["ok"] is False
    assert snap["providers"]["ollama"]["status"] == "probe_exception"
    assert "error_detail" in snap["providers"]["ollama"]


def test_provider_observer_probe_timeout_does_not_propagate():
    import socket
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    cfg = {"provider_observer_enabled": True}
    urls = {"lmstudio": "http://dead-host:1234/v1"}

    with patch("agent.services.provider_observer_service.probe_lmstudio_runtime", side_effect=TimeoutError("timed out")):
        snap = svc.snapshot(agent_config=cfg, provider_urls=urls)

    assert snap["providers"]["lmstudio"]["ok"] is False
    assert snap["providers"]["lmstudio"]["status"] == "probe_exception"


def test_provider_observer_timeout_clamped_to_max():
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    # timeout_seconds max is 15
    raw_timeout = svc._cfg_int({"provider_observer_timeout_seconds": 9999}, "provider_observer_timeout_seconds", 3, 1, 15)
    assert raw_timeout == 15


def test_provider_observer_timeout_clamped_to_min():
    from agent.services.provider_observer_service import ProviderObserverService

    svc = ProviderObserverService()
    raw_timeout = svc._cfg_int({"provider_observer_timeout_seconds": 0}, "provider_observer_timeout_seconds", 3, 1, 15)
    assert raw_timeout == 1


# CPR-002: profile availability validation
class TestProfileAvailabilityValidation:
    def test_unknown_profile_returns_structural_valid(self):
        from agent.services.config_profile_service import get_config_profile_service
        svc = get_config_profile_service()
        result = svc.validate_profile_availability(None)
        assert result["validation_level"] == "structural_valid"
        assert result["errors"] == []

    def test_profile_without_provider_returns_structural_valid(self, monkeypatch):
        from agent.services.config_profile_service import get_config_profile_service, ConfigProfile, _DEFAULT_PROFILES
        svc = get_config_profile_service()
        # opencode_preconfigured has no default_provider in overrides
        result = svc.validate_profile_availability("opencode_preconfigured")
        assert result["validation_level"] == "structural_valid"
        assert result["errors"] == []

    def test_unavailable_provider_warns_when_policy_allows(self, monkeypatch):
        from agent.services.config_profile_service import get_config_profile_service
        from unittest.mock import Mock
        svc = get_config_profile_service()

        mock_snapshot = {"providers": {"ollama": {"runtime": {"ok": False, "status": "connection_error"}}}}
        monkeypatch.setattr(
            "agent.services.provider_observer_service.ProviderObserverService.snapshot",
            Mock(return_value=mock_snapshot),
        )
        result = svc.validate_profile_availability(
            "ananta_ollama_local", block_on_unavailable=False
        )
        assert result["validation_level"] == "provider_unavailable"
        assert len(result["warnings"]) >= 1
        assert result["errors"] == []

    def test_unavailable_provider_errors_when_policy_blocks(self, monkeypatch):
        from agent.services.config_profile_service import get_config_profile_service
        from unittest.mock import Mock
        svc = get_config_profile_service()

        mock_snapshot = {"providers": {"ollama": {"runtime": {"ok": False, "status": "connection_error"}}}}
        monkeypatch.setattr(
            "agent.services.provider_observer_service.ProviderObserverService.snapshot",
            Mock(return_value=mock_snapshot),
        )
        result = svc.validate_profile_availability(
            "ananta_ollama_local", block_on_unavailable=True
        )
        assert result["validation_level"] == "provider_unavailable"
        assert len(result["errors"]) >= 1
        assert result["warnings"] == []

    def test_available_provider_returns_observable(self, monkeypatch):
        from agent.services.config_profile_service import get_config_profile_service
        from unittest.mock import Mock
        svc = get_config_profile_service()

        mock_snapshot = {"providers": {"ollama": {"runtime": {"ok": True, "status": "models_loaded"}}}}
        monkeypatch.setattr(
            "agent.services.provider_observer_service.ProviderObserverService.snapshot",
            Mock(return_value=mock_snapshot),
        )
        result = svc.validate_profile_availability("ananta_ollama_local")
        assert result["validation_level"] == "provider_observable"
        assert result["errors"] == []
        assert result["warnings"] == []

    def test_probe_exception_is_a_warning_not_error(self, monkeypatch):
        from agent.services.config_profile_service import get_config_profile_service
        from unittest.mock import Mock
        svc = get_config_profile_service()

        monkeypatch.setattr(
            "agent.services.provider_observer_service.ProviderObserverService.snapshot",
            Mock(side_effect=RuntimeError("observer down")),
        )
        result = svc.validate_profile_availability("ananta_ollama_local")
        assert result["errors"] == []
        assert len(result["warnings"]) >= 1
        assert "observer down" in result["warnings"][0]
