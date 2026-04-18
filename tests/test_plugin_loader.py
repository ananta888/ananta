from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.plugin_loader import load_plugins
from agent.services.evolution import get_evolution_provider_registry


def test_load_plugins_from_plugin_dirs(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_plugin.py"
    mod.write_text(
        "def init_app(app):\n    app.config['PLUGIN_DEMO'] = True\n",
        encoding="utf-8",
    )

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "demo_plugin" in loaded
        assert app.config.get("PLUGIN_DEMO") is True
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins


def test_load_plugins_registers_declared_evolution_provider(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_evolution_provider.py"
    mod.write_text(
        "\n".join(
            [
                "from agent.services.evolution import (",
                "    EvolutionCapability,",
                "    EvolutionContext,",
                "    EvolutionEngine,",
                "    EvolutionResult,",
                ")",
                "",
                "class DemoEvolutionProvider(EvolutionEngine):",
                "    @property",
                "    def provider_name(self):",
                "        return 'plugin-evolution'",
                "",
                "    @property",
                "    def capabilities(self):",
                "        return [EvolutionCapability.ANALYZE]",
                "",
                "    def analyze(self, context: EvolutionContext) -> EvolutionResult:",
                "        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)",
                "",
                "evolution_provider = DemoEvolutionProvider()",
                "evolution_provider_default = True",
            ]
        ),
        encoding="utf-8",
    )

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "demo_evolution_provider" in loaded
        assert registry.resolve().provider_name == "plugin-evolution"
        assert "plugin-evolution" in app.extensions["evolution_providers"]
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins
        registry.clear()


def test_load_plugins_registers_evolution_provider_from_init_app(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_evolution_init_provider.py"
    mod.write_text(
        "\n".join(
            [
                "from agent.services.evolution import (",
                "    EvolutionCapability,",
                "    EvolutionContext,",
                "    EvolutionEngine,",
                "    EvolutionResult,",
                "    register_evolution_provider,",
                ")",
                "",
                "class DemoEvolutionProvider(EvolutionEngine):",
                "    @property",
                "    def provider_name(self):",
                "        return 'plugin-init-evolution'",
                "",
                "    @property",
                "    def capabilities(self):",
                "        return [EvolutionCapability.ANALYZE]",
                "",
                "    def analyze(self, context: EvolutionContext) -> EvolutionResult:",
                "        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)",
                "",
                "def init_app(app):",
                "    register_evolution_provider(DemoEvolutionProvider(), app=app, default=True)",
            ]
        ),
        encoding="utf-8",
    )

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "demo_evolution_init_provider" in loaded
        assert registry.resolve().provider_name == "plugin-init-evolution"
        assert "plugin-init-evolution" in app.extensions["evolution_providers"]
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins
        registry.clear()


def test_load_plugins_skips_duplicate_declarative_provider_after_init_app(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_duplicate_evolution_provider.py"
    mod.write_text(
        "\n".join(
            [
                "from agent.services.evolution import (",
                "    EvolutionCapability,",
                "    EvolutionContext,",
                "    EvolutionEngine,",
                "    EvolutionResult,",
                "    register_evolution_provider,",
                ")",
                "",
                "class DemoEvolutionProvider(EvolutionEngine):",
                "    @property",
                "    def provider_name(self):",
                "        return 'duplicate-evolution'",
                "",
                "    @property",
                "    def capabilities(self):",
                "        return [EvolutionCapability.ANALYZE]",
                "",
                "    def analyze(self, context: EvolutionContext) -> EvolutionResult:",
                "        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)",
                "",
                "def init_app(app):",
                "    register_evolution_provider(DemoEvolutionProvider(), app=app, default=True)",
                "",
                "evolution_provider = DemoEvolutionProvider()",
            ]
        ),
        encoding="utf-8",
    )

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert "demo_duplicate_evolution_provider" in loaded
        assert registry.resolve().provider_name == "duplicate-evolution"
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins
        registry.clear()


def test_load_plugins_rejects_declarative_provider_name_conflict(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    provider_source = [
        "from agent.services.evolution import (",
        "    EvolutionCapability,",
        "    EvolutionContext,",
        "    EvolutionEngine,",
        "    EvolutionResult,",
        ")",
        "",
        "class DemoEvolutionProvider(EvolutionEngine):",
        "    @property",
        "    def provider_name(self):",
        "        return 'shared-evolution'",
        "",
        "    @property",
        "    def capabilities(self):",
        "        return [EvolutionCapability.ANALYZE]",
        "",
        "    def analyze(self, context: EvolutionContext) -> EvolutionResult:",
        "        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)",
        "",
        "evolution_provider = DemoEvolutionProvider()",
    ]
    (plugin_dir / "demo_conflict_a.py").write_text("\n".join(provider_source), encoding="utf-8")
    (plugin_dir / "demo_conflict_b.py").write_text("\n".join(provider_source), encoding="utf-8")

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert loaded == ["demo_conflict_a"]
        assert registry.resolve("shared-evolution").provider_name == "shared-evolution"
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins
        registry.clear()


def test_load_plugins_allows_explicit_declarative_provider_replace(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    provider_source = [
        "from agent.services.evolution import (",
        "    EvolutionCapability,",
        "    EvolutionContext,",
        "    EvolutionEngine,",
        "    EvolutionResult,",
        ")",
        "",
        "class DemoEvolutionProvider(EvolutionEngine):",
        "    @property",
        "    def provider_name(self):",
        "        return 'replace-evolution'",
        "",
        "    @property",
        "    def capabilities(self):",
        "        return [EvolutionCapability.ANALYZE]",
        "",
        "    def analyze(self, context: EvolutionContext) -> EvolutionResult:",
        "        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)",
        "",
        "evolution_provider = DemoEvolutionProvider()",
    ]
    (plugin_dir / "demo_replace_a.py").write_text("\n".join(provider_source), encoding="utf-8")
    (plugin_dir / "demo_replace_b.py").write_text(
        "\n".join([*provider_source, "evolution_provider_replace = True"]),
        encoding="utf-8",
    )

    old_dirs = settings.plugin_dirs
    old_plugins = settings.plugins
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        settings.plugin_dirs = str(plugin_dir)
        settings.plugins = ""
        app = Flask(__name__)
        loaded = load_plugins(app)
        assert loaded == ["demo_replace_a", "demo_replace_b"]
        assert registry.resolve("replace-evolution").provider_name == "replace-evolution"
    finally:
        settings.plugin_dirs = old_dirs
        settings.plugins = old_plugins
        registry.clear()
