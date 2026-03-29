from __future__ import annotations

import json
from pathlib import Path


SECTION_KEYS = {
    "filters",
    "limits",
    "modes",
    "resolution",
    "cache",
    "output",
    "flags",
}

KEY_ALIASES = {
    "include_globs": "include_glob",
    "exclude_globs": "exclude_glob",
    "generated_comment_markers": "generated_comment_marker",
}

PATH_KEYS = {"root", "out", "cache_file", "error_log_file"}


def _load_yaml_config(path: Path) -> dict:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise SystemExit(
            "YAML-Konfigurationsdateien erfordern PyYAML. "
            "Installiere 'PyYAML' oder nutze eine JSON-Konfiguration."
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def load_profile_config(path: Path | None) -> tuple[dict, Path | None]:
    if path is None:
        return {}, None
    resolved = path.resolve()
    if not resolved.exists():
        raise SystemExit(f"Konfigurationsdatei nicht gefunden: {resolved}")
    suffix = resolved.suffix.lower()
    if suffix == ".json":
        data = json.loads(resolved.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        data = _load_yaml_config(resolved)
    else:
        raise SystemExit(f"Nicht unterstütztes Konfigurationsformat: {resolved.suffix}")
    if not isinstance(data, dict):
        raise SystemExit("Konfigurationsdatei muss ein Objekt/Mapping enthalten")
    return normalize_profile_config(data, resolved.parent), resolved


def normalize_profile_config(raw: dict, base_dir: Path) -> dict:
    normalized: dict = {}
    for key, value in raw.items():
        target_key = KEY_ALIASES.get(key, key)
        if key in SECTION_KEYS:
            if not isinstance(value, dict):
                raise SystemExit(f"Konfigurationsabschnitt '{key}' muss ein Mapping sein")
            for nested_key, nested_value in value.items():
                nested_target = KEY_ALIASES.get(nested_key, nested_key)
                normalized[nested_target] = _normalize_value(nested_target, nested_value, base_dir)
            continue
        normalized[target_key] = _normalize_value(target_key, value, base_dir)
    return normalized


def _normalize_value(key: str, value, base_dir: Path):
    if key in PATH_KEYS and isinstance(value, str) and value:
        if "{out}" in value:
            return value
        return str((base_dir / value).resolve()) if not Path(value).is_absolute() else value
    if key in {"include_glob", "exclude_glob", "generated_comment_marker"} and isinstance(value, str):
        return [value]
    return value
