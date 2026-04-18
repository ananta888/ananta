import logging
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.plugin_loader import load_plugins
from agent.services.evolution import get_evolution_provider_registry


def test_load_plugins_handles_crashy_plugin(tmp_path: Path, caplog):
    """
    Sicherstellen, dass ein abstuerzendes Plugin nicht den gesamten Ladevorgang stoppt (PLG-072).
    """
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Ein gesundes Plugin
    (plugin_dir / "healthy_plugin.py").write_text("def init_app(app): pass", encoding="utf-8")

    # Ein kaputtes Plugin
    (plugin_dir / "crashy_plugin.py").write_text("raise RuntimeError('Boom')", encoding="utf-8")

    old_dirs = settings.plugin_dirs
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        with caplog.at_level(logging.ERROR):
            loaded = load_plugins(app)
            assert "healthy_plugin" in loaded
            assert "crashy_plugin" not in loaded
            report = app.extensions["plugin_startup_report"]
            by_name = {entry["name"]: entry for entry in report["entries"]}
            assert by_name["healthy_plugin"]["status"] == "loaded"
            assert by_name["healthy_plugin"]["registration_mode"] == "init_app"
            assert by_name["crashy_plugin"]["status"] == "failed"
            assert report["errors"][0]["name"] == "crashy_plugin"
            assert any("Fehler beim Laden des Plugins crashy_plugin" in record.message for record in caplog.records)
    finally:
        settings.plugin_dirs = old_dirs

def test_load_plugins_multiple_providers_in_one_file(tmp_path: Path):
    """
    Testet die Registrierung mehrerer Provider aus einem einzigen Plugin-Modul.
    """
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "multi_provider.py"
    mod.write_text("\n".join([
        "from agent.services.evolution.engine import EvolutionEngine",
        "from agent.services.evolution.models import EvolutionCapability",
        "class P1(EvolutionEngine):",
        "    @property\n    def provider_name(self): return 'p1'",
        "    @property\n    def capabilities(self): return [EvolutionCapability.ANALYZE]",
        "    def analyze(self, ctx): pass",
        "class P2(EvolutionEngine):",
        "    @property\n    def provider_name(self): return 'p2'",
        "    @property\n    def capabilities(self): return [EvolutionCapability.ANALYZE]",
        "    def analyze(self, ctx): pass",
        "evolution_providers = [P1(), P2()]"
    ]), encoding="utf-8")

    old_dirs = settings.plugin_dirs
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "multi_provider" in loaded
        entry = next(e for e in app.extensions["plugin_startup_report"]["entries"] if e["name"] == "multi_provider")
        assert entry["registration_mode"] == "evolution_provider"
        assert entry["evolution_provider_count"] == 2
        assert registry.contains("p1")
        assert registry.contains("p2")
    finally:
        settings.plugin_dirs = old_dirs
        registry.clear()

def test_evolution_provider_versioning_support(tmp_path: Path):
    """
    Prueft, ob die Version eines Providers korrekt registriert wird.
    """
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "versioned_provider.py"
    mod.write_text("\n".join([
        "from agent.services.evolution.engine import EvolutionEngine",
        "class VersionedP(EvolutionEngine):",
        "    @property\n    def provider_name(self): return 'versioned'",
        "    @property\n    def version(self): return '1.2.3'",
        "    @property\n    def capabilities(self): return ['analyze']",
        "    def analyze(self, ctx): pass",
        "evolution_provider = VersionedP()"
    ]), encoding="utf-8")

    old_dirs = settings.plugin_dirs
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        app = Flask(__name__)
        load_plugins(app)
        provider = registry.get("versioned")
        assert provider.version == "1.2.3"

        # Pruefe Descriptor
        desc = provider.describe()
        assert desc.version == "1.2.3"
    finally:
        settings.plugin_dirs = old_dirs
        registry.clear()
