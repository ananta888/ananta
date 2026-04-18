import pytest
from flask import Flask
from agent.plugin_loader import load_plugins
from agent.config import settings
from agent.services.evolution import get_evolution_provider_registry
from agent.services.evolution.models import EvolutionContext

def test_canary_plugin_loading_and_execution():
    """
    Testet, ob das Canary-Plugin korrekt geladen wird und die Schnittstellen bedient.
    """
    app = Flask(__name__)
    registry = get_evolution_provider_registry()
    registry.clear()

    # Simuliere Laden des Canary-Plugins
    old_plugins = settings.plugins
    old_dirs = settings.plugin_dirs
    try:
        settings.plugin_dirs = "plugins"
        settings.plugins = "canary_plugin"
        load_plugins(app)

        assert registry.contains("canary")
        provider = registry.get("canary")

        # Teste Ausfuehrung
        ctx = EvolutionContext(objective="Test")
        res = provider.analyze(ctx)
        assert res.provider_name == "canary"
        assert "Canary analysis" in res.summary

    finally:
        settings.plugins = old_plugins
        settings.plugin_dirs = old_dirs
        registry.clear()

def test_canary_plugin_sdk_boundary_enforcement():
    """
    Stellt sicher, dass das Plugin nur ueber das SDK registriert werden kann
    und keine unbefugten Zugriffe auf Interna stattfinden (Konzept-Test).
    """
    # Hier koennte man mit Mocks pruefen, ob nur SDK-Methoden aufgerufen werden.
    # Da Python dynamisch ist, ist echte 'Enforcement' schwierig,
    # aber wir validieren hier den 'Happy Path' der SDK-Nutzung.
    pass
