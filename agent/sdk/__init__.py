from __future__ import annotations
from flask import Flask
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class EvolutionEngine(Protocol):
    """Protokoll für Evolution-Provider, damit Plugins diese implementieren können."""
    provider_name: str
    def analyze(self, context: Any) -> Any: ...
    def validate(self, context: Any, proposal: Any) -> Any: ...
    def apply(self, context: Any, proposal: Any) -> Any: ...
    def describe(self) -> Any: ...
    def supports(self, capability: Any) -> bool: ...

class AnantaSDK:
    """Zentrale SDK-Klasse für Ananta-Plugins."""
    def __init__(self, app: Flask):
        self.app = app

    def register_evolution_provider(self, engine: EvolutionEngine, default: bool = False, replace: bool = False):
        """Registriert einen Evolution-Provider im System."""
        from agent.services.evolution.registry import register_evolution_provider
        return register_evolution_provider(engine, app=self.app, default=default, replace=replace)

    def register_blueprint(self, blueprint):
        """Registriert ein Flask-Blueprint (für neue API-Routen)."""
        self.app.register_blueprint(blueprint)

    def get_config(self, section: str | None = None) -> dict[str, Any]:
        """Gibt die Agenten-Konfiguration zurück."""
        cfg = self.app.config.get("AGENT_CONFIG") or {}
        if section:
            return cfg.get(section) or {}
        return cfg

def get_sdk(app: Flask) -> AnantaSDK:
    """Gibt eine Instanz des SDKs für die gegebene App zurück."""
    if "ananta_sdk" not in app.extensions:
        app.extensions["ananta_sdk"] = AnantaSDK(app)
    return app.extensions["ananta_sdk"]
