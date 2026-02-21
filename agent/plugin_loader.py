import importlib
import logging
import sys
from pathlib import Path
from typing import Iterable

from flask import Flask

from agent.config import settings


def _iter_enabled_plugins() -> list[str]:
    return [name.strip() for name in (settings.plugins or "").split(",") if name.strip()]


def _iter_plugin_dirs() -> list[Path]:
    dirs: list[Path] = []
    for raw in (settings.plugin_dirs or "").split(","):
        val = raw.strip()
        if not val:
            continue
        p = Path(val).resolve()
        if p.exists() and p.is_dir():
            dirs.append(p)
    return dirs


def _discover_plugins_in_dirs(paths: Iterable[Path]) -> list[str]:
    discovered: list[str] = []
    for path in paths:
        sys.path.insert(0, str(path))
        for child in path.iterdir():
            if child.name.startswith("_"):
                continue
            if child.is_dir() and (child / "__init__.py").exists():
                discovered.append(child.name)
            elif child.is_file() and child.suffix == ".py":
                discovered.append(child.stem)
    return sorted(set(discovered))


def load_plugins(app: Flask) -> list[str]:
    loaded: list[str] = []
    names = set(_iter_enabled_plugins())
    names.update(_discover_plugins_in_dirs(_iter_plugin_dirs()))
    for mod_name in sorted(names):
        try:
            module = importlib.import_module(mod_name)
            if hasattr(module, "init_app"):
                module.init_app(app)
                loaded.append(mod_name)
                logging.info("Plugin geladen: %s (init_app)", mod_name)
                continue
            if hasattr(module, "bp"):
                app.register_blueprint(module.bp)
                loaded.append(mod_name)
                logging.info("Plugin geladen: %s (bp)", mod_name)
                continue
            if hasattr(module, "blueprint"):
                app.register_blueprint(module.blueprint)
                loaded.append(mod_name)
                logging.info("Plugin geladen: %s (blueprint)", mod_name)
                continue
            logging.warning("Plugin %s hat keine init_app/bp/blueprint", mod_name)
        except Exception as e:
            logging.error("Fehler beim Laden des Plugins %s: %s", mod_name, e)
    return loaded
