import importlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from flask import Flask

from agent.config import settings


@dataclass
class PluginReportEntry:
    name: str
    status: str
    registration_mode: str | None = None
    evolution_provider_count: int = 0
    manifest: dict[str, Any] | None = None
    error: str | None = None
    contained: bool = False
    duration_seconds: float = 0.0


@dataclass
class PluginStartupReport:
    loaded: list[str] = field(default_factory=list)
    entries: list[PluginReportEntry] = field(default_factory=list)

    def add(self, entry: PluginReportEntry) -> None:
        self.entries.append(entry)
        if entry.status == "loaded":
            self.loaded.append(entry.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded": list(self.loaded),
            "entries": [
                {
                    "name": entry.name,
                    "status": entry.status,
                    "registration_mode": entry.registration_mode,
                    "evolution_provider_count": entry.evolution_provider_count,
                    "manifest": entry.manifest,
                    "error": entry.error,
                    "contained": entry.contained,
                    "duration_seconds": round(entry.duration_seconds, 6),
                }
                for entry in self.entries
            ],
            "errors": [
                {
                    "name": entry.name,
                    "error": entry.error,
                }
                for entry in self.entries
                if entry.status == "failed"
            ],
        }


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


def _manifest_path_for_plugin(mod_name: str) -> Path | None:
    for plugin_dir in _iter_plugin_dirs():
        package_manifest = plugin_dir / mod_name / "ananta-plugin.json"
        if package_manifest.exists():
            return package_manifest
        module_manifest = plugin_dir / f"{mod_name}.plugin.json"
        if module_manifest.exists():
            return module_manifest
    return None


def _read_plugin_manifest(mod_name: str) -> dict[str, Any]:
    manifest_path = _manifest_path_for_plugin(mod_name)
    if not manifest_path:
        return {}
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("plugin_manifest_must_be_object")
    declared_name = str(raw.get("name") or mod_name).strip()
    if declared_name and declared_name != mod_name:
        raise ValueError(f"plugin_manifest_name_mismatch:{declared_name}")
    return raw


def _manifest_enabled(manifest: dict[str, Any]) -> bool:
    if not manifest:
        return True
    return bool(manifest.get("enabled", True))


def _coerce_provider_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "provider_name", "") or "").strip().lower()


def _register_declared_evolution_providers(app: Flask, module: Any, *, allow_existing: bool = False) -> int:
    from agent.sdk import get_sdk

    sdk = get_sdk(app)
    provider_source = None
    if hasattr(module, "get_evolution_providers"):
        provider_source = module.get_evolution_providers(app)
    elif hasattr(module, "evolution_providers"):
        provider_source = module.evolution_providers
    elif hasattr(module, "evolution_provider"):
        provider_source = module.evolution_provider

    providers = _coerce_provider_list(provider_source)
    if not providers:
        return 0

    from agent.services.evolution import get_evolution_provider_registry

    default = bool(getattr(module, "evolution_provider_default", False))
    replace = bool(getattr(module, "evolution_provider_replace", False))
    registry = get_evolution_provider_registry()
    app_provider_names = {str(name).strip().lower() for name in app.extensions.get("evolution_providers", set())}
    registered_count = 0
    for index, provider in enumerate(providers):
        name = _provider_name(provider)
        if not name:
            raise ValueError("declared_evolution_provider_name_required")
        already_registered = name in app_provider_names or registry.contains(name)
        if already_registered and not replace:
            if allow_existing:
                logging.warning("Deklarativer Evolution-Provider %s bereits durch init_app registriert", name)
                continue
            raise ValueError(f"declared_evolution_provider_conflict:{name}")
        sdk.register_evolution_provider(provider, default=default and index == 0, replace=replace)
        app_provider_names.add(name)
        registered_count += 1
    return registered_count


def load_plugins(app: Flask) -> list[str]:
    report = PluginStartupReport()
    names = set(_iter_enabled_plugins())
    names.update(_discover_plugins_in_dirs(_iter_plugin_dirs()))
    for mod_name in sorted(names):
        started = time.perf_counter()
        manifest: dict[str, Any] = {}
        try:
            manifest = _read_plugin_manifest(mod_name)
            if not _manifest_enabled(manifest):
                report.add(
                    PluginReportEntry(
                        name=mod_name,
                        status="disabled",
                        registration_mode="manifest",
                        manifest=manifest,
                        contained=True,
                        duration_seconds=time.perf_counter() - started,
                    )
                )
                logging.info("Plugin deaktiviert durch Manifest: %s", mod_name)
                continue
            module = importlib.import_module(mod_name)
            evolution_provider_count = 0
            if hasattr(module, "init_app"):
                module.init_app(app)
                evolution_provider_count = _register_declared_evolution_providers(app, module, allow_existing=True)
                report.add(
                    PluginReportEntry(
                        name=mod_name,
                        status="loaded",
                        registration_mode="init_app",
                        evolution_provider_count=evolution_provider_count,
                        manifest=manifest or None,
                        duration_seconds=time.perf_counter() - started,
                    )
                )
                logging.info("Plugin geladen: %s (init_app)", mod_name)
                continue
            if hasattr(module, "bp"):
                app.register_blueprint(module.bp)
                evolution_provider_count = _register_declared_evolution_providers(app, module)
                report.add(
                    PluginReportEntry(
                        name=mod_name,
                        status="loaded",
                        registration_mode="bp",
                        evolution_provider_count=evolution_provider_count,
                        manifest=manifest or None,
                        duration_seconds=time.perf_counter() - started,
                    )
                )
                logging.info("Plugin geladen: %s (bp)", mod_name)
                continue
            if hasattr(module, "blueprint"):
                app.register_blueprint(module.blueprint)
                evolution_provider_count = _register_declared_evolution_providers(app, module)
                report.add(
                    PluginReportEntry(
                        name=mod_name,
                        status="loaded",
                        registration_mode="blueprint",
                        evolution_provider_count=evolution_provider_count,
                        manifest=manifest or None,
                        duration_seconds=time.perf_counter() - started,
                    )
                )
                logging.info("Plugin geladen: %s (blueprint)", mod_name)
                continue
            evolution_provider_count = _register_declared_evolution_providers(app, module)
            if evolution_provider_count:
                report.add(
                    PluginReportEntry(
                        name=mod_name,
                        status="loaded",
                        registration_mode="evolution_provider",
                        evolution_provider_count=evolution_provider_count,
                        manifest=manifest or None,
                        duration_seconds=time.perf_counter() - started,
                    )
                )
                logging.info("Plugin geladen: %s (%s evolution providers)", mod_name, evolution_provider_count)
                continue
            report.add(
                PluginReportEntry(
                    name=mod_name,
                    status="ignored",
                    registration_mode="none",
                    manifest=manifest or None,
                    duration_seconds=time.perf_counter() - started,
                )
            )
            logging.warning("Plugin %s hat keine init_app/bp/blueprint", mod_name)
        except Exception as e:
            report.add(
                PluginReportEntry(
                    name=mod_name,
                    status="failed",
                    manifest=manifest or None,
                    error=str(e),
                    contained=True,
                    duration_seconds=time.perf_counter() - started,
                )
            )
            logging.error("Fehler beim Laden des Plugins %s: %s", mod_name, e)
    app.extensions["loaded_plugins"] = list(report.loaded)
    app.extensions["plugin_startup_report"] = report.to_dict()
    return list(report.loaded)
