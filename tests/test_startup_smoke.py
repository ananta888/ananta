import pytest
import os
from flask import Flask
from agent.ai_agent import create_app
from agent.config import Settings, settings
from agent.services.evolution import get_evolution_provider_registry

def _reload_settings():
    # Erzeugt eine neue Settings-Instanz aus dem aktuellen Environment
    new_settings = Settings()
    # Wir muessen die globale Instanz patchen, da viele Module sie importiert haben
    # Das ist hacky fuer Tests, aber effektiv fuer Smoke-Checks
    for field in new_settings.model_fields:
        setattr(settings, field, getattr(new_settings, field))

def test_startup_hub_profile():
    """Smoke check fuer das Hub-Profil (ROLE=hub)."""
    os.environ["ROLE"] = "hub"
    os.environ["EVOLVER_ENABLED"] = "false"
    _reload_settings()
    get_evolution_provider_registry().clear()
    try:
        app = create_app()
        assert isinstance(app, Flask)
        assert settings.role == "hub"
        with app.test_client() as client:
            resp = client.get("/health")
            assert resp.status_code in [200, 401]
    finally:
        if "ROLE" in os.environ: del os.environ["ROLE"]

def test_startup_worker_profile():
    """Smoke check fuer das Worker-Profil (ROLE=worker)."""
    os.environ["ROLE"] = "worker"
    _reload_settings()
    get_evolution_provider_registry().clear()
    try:
        app = create_app()
        assert isinstance(app, Flask)
        assert settings.role == "worker"
    finally:
        if "ROLE" in os.environ: del os.environ["ROLE"]

def test_startup_evolver_profile():
    """Smoke check fuer Profil mit aktiviertem Evolver."""
    os.environ["EVOLVER_ENABLED"] = "true"
    _reload_settings()
    get_evolution_provider_registry().clear()
    try:
        app = create_app()
        # settings.evolver_enabled sollte True sein
        assert settings.evolver_enabled is True
    finally:
        if "EVOLVER_ENABLED" in os.environ: del os.environ["EVOLVER_ENABLED"]

def test_startup_plugin_heavy_profile():
    """Smoke check fuer Profil mit vielen Plugins."""
    os.environ["AGENT_PLUGIN_DIRS"] = "plugins"
    os.environ["AGENT_PLUGINS"] = "canary_plugin"
    _reload_settings()
    get_evolution_provider_registry().clear()
    try:
        app = create_app()
        # In PLG-072 haben wir loaded_plugins hinzugefuegt
        loaded = app.extensions.get("loaded_plugins", [])
        assert "canary_plugin" in loaded
    finally:
        if "AGENT_PLUGIN_DIRS" in os.environ: del os.environ["AGENT_PLUGIN_DIRS"]
        if "AGENT_PLUGINS" in os.environ: del os.environ["AGENT_PLUGINS"]
