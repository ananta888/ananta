from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.plugin_loader import load_plugins


def test_load_plugins_from_plugin_dirs(tmp_path: Path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    mod = plugin_dir / "demo_plugin.py"
    mod.write_text(
        "def init_app(app):\n"
        "    app.config['PLUGIN_DEMO'] = True\n",
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
