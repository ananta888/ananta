from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _as_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def _as_int(val: Any, default: int) -> int:
    try:
        return int(str(val))
    except Exception:
        return default


def _load_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise ValueError(f"Fehler beim Laden der JSON-Datei '{path}': {e}")


def _validate_url(name: str, value: Optional[str]) -> None:
    if value is None or value == "":
        return
    parsed = urlparse(value)
    if not (parsed.scheme and parsed.netloc):
        raise ValueError(f"Ungültige URL für {name}: '{value}'")


@dataclass
class RetryConfig:
    total: int = 3
    backoff_factor: float = 0.5
    status_forcelist: List[int] = field(default_factory=lambda: [502, 503, 504])

    @staticmethod
    def from_mapping(data: Dict[str, Any] | None) -> "RetryConfig":
        data = data or {}
        total = _as_int(data.get("total", 3), 3)
        try:
            backoff = float(data.get("backoff_factor", 0.5))
        except Exception:
            backoff = 0.5
        sfl = data.get("status_forcelist", [502, 503, 504])
        if not isinstance(sfl, list):
            sfl = [502, 503, 504]
        sfl = [int(x) for x in sfl]
        if total < 0:
            raise ValueError("Retry.total darf nicht negativ sein")
        if backoff < 0:
            raise ValueError("Retry.backoff_factor darf nicht negativ sein")
        return RetryConfig(total=total, backoff_factor=backoff, status_forcelist=sfl)


@dataclass
class Settings:
    # Core URLs
    controller_url: str = "http://controller:8081"
    ollama_url: str = "http://localhost:11434/api/generate"
    lmstudio_url: str = "http://localhost:1234/v1/completions"
    openai_url: str = "https://api.openai.com/v1/chat/completions"
    openai_api_key: Optional[str] = None

    # Agent settings
    agent_name: str = "Architect"
    agent_startup_delay: int = 3
    port: int = 5000

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # HTTP defaults
    http_timeout_get: float = 10.0
    http_timeout_post: float = 15.0
    retry: RetryConfig = field(default_factory=RetryConfig)

    # Feature flags (generic)
    features: Dict[str, bool] = field(default_factory=dict)

    def validate(self) -> None:
        # URLs
        _validate_url("controller_url", self.controller_url)
        _validate_url("ollama_url", self.ollama_url)
        _validate_url("lmstudio_url", self.lmstudio_url)
        _validate_url("openai_url", self.openai_url)

        # Log level
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if str(self.log_level).upper() not in valid_levels:
            raise ValueError(f"Ungültiger Log-Level: '{self.log_level}'. Erlaubt: {sorted(valid_levels)}")

        # Timeouts
        try:
            self.http_timeout_get = float(self.http_timeout_get)
            self.http_timeout_post = float(self.http_timeout_post)
        except Exception:
            raise ValueError("Timeouts müssen numerisch sein")
        if self.http_timeout_get <= 0 or self.http_timeout_post <= 0:
            raise ValueError("Timeouts müssen > 0 sein")

        # Startup delay
        self.agent_startup_delay = _as_int(self.agent_startup_delay, 3)
        if self.agent_startup_delay < 0:
            raise ValueError("agent_startup_delay darf nicht negativ sein")

        # Port
        self.port = _as_int(self.port, 5000)
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port muss zwischen 1 und 65535 liegen")

        # Retry already validated in factory

    @staticmethod
    def _merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(a)
        for k, v in (b or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = Settings._merge(out[k], v)
            else:
                out[k] = v
        return out

    @classmethod
    def from_sources(
        cls,
        defaults_path: Optional[str] = None,
        env_json_path: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "Settings":
        """Lädt Settings mit Priorität: defaults.json < env.json < Environment.

        - defaults_path: Pfad zu defaults.json (optional; Standard: src/config/defaults.json)
        - env_json_path: expliziter Pfad zu env.json (optional; Standard: AI_AGENT_ENV_FILE oder src/config/env.json)
        - env: explizites Environment-Dict (nur für Tests)
        """
        env = env or dict(os.environ)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if not defaults_path:
            defaults_path = os.path.join(base_dir, "defaults.json")

        # Load defaults and env.json
        data = _load_json_file(defaults_path)
        if env_json_path is None:
            env_json_path = env.get("AI_AGENT_ENV_FILE") or os.path.join(base_dir, "env.json")
        env_data = _load_json_file(env_json_path) if env_json_path else {}
        merged = cls._merge(data, env_data)

        # Apply environment overrides (both modern AI_AGENT_* and legacy names)
        def _pick(*names: str, default: Any = None) -> Any:
            for n in names:
                if n in env and env[n] != "":
                    return env[n]
            return default

        merged.update({
            "controller_url": _pick("AI_AGENT_CONTROLLER_URL", "CONTROLLER_URL", default=merged.get("controller_url")),
            "ollama_url": _pick("AI_AGENT_OLLAMA_URL", default=merged.get("ollama_url")),
            "lmstudio_url": _pick("AI_AGENT_LMSTUDIO_URL", default=merged.get("lmstudio_url")),
            "openai_url": _pick("AI_AGENT_OPENAI_URL", default=merged.get("openai_url")),
            "openai_api_key": _pick("AI_AGENT_OPENAI_API_KEY", "OPENAI_API_KEY", default=merged.get("openai_api_key")),
            "log_level": _pick("AI_AGENT_LOG_LEVEL", default=merged.get("log_level", "INFO")),
            "log_json": _as_bool(_pick("AI_AGENT_LOG_JSON", default=merged.get("log_json", False))),
            "agent_name": _pick("AI_AGENT_NAME", "AGENT_NAME", default=merged.get("agent_name", "Architect")),
            "agent_startup_delay": _as_int(_pick("AI_AGENT_STARTUP_DELAY", "AGENT_STARTUP_DELAY", default=merged.get("agent_startup_delay", 3)), 3),
            "port": _as_int(_pick("AI_AGENT_PORT", "PORT", default=merged.get("port", 5000)), 5000),
        })

        # HTTP/timeouts
        timeouts = merged.get("http", {})
        if isinstance(timeouts, dict):
            if "timeout_get" in timeouts:
                merged["http_timeout_get"] = float(timeouts["timeout_get"])  # type: ignore
            if "timeout_post" in timeouts:
                merged["http_timeout_post"] = float(timeouts["timeout_post"])  # type: ignore

        # Retry
        merged["retry"] = RetryConfig.from_mapping(merged.get("retry"))

        # Features (flatten env AI_AGENT_FEATURE_*)
        features: Dict[str, bool] = {}
        for k, v in env.items():
            if k.startswith("AI_AGENT_FEATURE_"):
                features[k[len("AI_AGENT_FEATURE_"):].lower()] = _as_bool(v)
        merged["features"] = {**merged.get("features", {}), **features}

        # Build instance
        inst = cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})
        inst.validate()
        return inst


_CACHED: Optional[Settings] = None


def load_settings(force_reload: bool = False) -> Settings:
    global _CACHED
    if _CACHED is None or force_reload:
        _CACHED = Settings.from_sources()
    return _CACHED

