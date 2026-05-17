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
