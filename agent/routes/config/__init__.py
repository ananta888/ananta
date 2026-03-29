from __future__ import annotations

import importlib.util
from pathlib import Path

from agent.routes.config.teams import teams_bp

_LEGACY_CONFIG_MODULE = None


def _load_legacy_config_module():
    global _LEGACY_CONFIG_MODULE
    if _LEGACY_CONFIG_MODULE is not None:
        return _LEGACY_CONFIG_MODULE

    legacy_path = Path(__file__).resolve().parent.parent / "config.py"
    spec = importlib.util.spec_from_file_location("agent.routes._legacy_config_module", legacy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy config routes from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LEGACY_CONFIG_MODULE = module
    return module


_legacy = _load_legacy_config_module()
config_bp = _legacy.config_bp
_LMSTUDIO_CATALOG_CACHE = _legacy._LMSTUDIO_CATALOG_CACHE
_LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = _legacy._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES
_list_lmstudio_candidates = _legacy._list_lmstudio_candidates
generate_text = _legacy.generate_text
time = _legacy.time


def _sync_legacy_exports():
    _legacy._LMSTUDIO_CATALOG_CACHE = _LMSTUDIO_CATALOG_CACHE
    _legacy._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = _LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES
    _legacy._list_lmstudio_candidates = _list_lmstudio_candidates
    _legacy.generate_text = generate_text


def register_config_blueprints(app):
    _sync_legacy_exports()

    @app.before_request
    def _refresh_config_route_exports():
        _sync_legacy_exports()

    app.register_blueprint(config_bp)
    app.register_blueprint(teams_bp)
