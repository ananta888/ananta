import enum
import json
import re
from typing import Any, Dict, List, Optional, Set, Union


class SensitiveDataClass(str, enum.Enum):
    TOKEN = "token"
    SECRET = "secret"
    CREDENTIAL = "credential"
    PATH = "path"
    INTERNAL_URL = "internal_url"
    PRIVATE_PROMPT = "private_prompt"
    IP_ADDRESS = "ip_address"
    SENSITIVE_FIELD = "sensitive_field"


class VisibilityLevel(int, enum.Enum):
    PUBLIC = 0  # Strengste Maskierung, nur absolut unbedenkliche Infos
    USER = 1    # Standard für Benutzer, moderate Maskierung (Secrets/Tokens weg)
    ADMIN = 2   # Für Admins, weniger Maskierung (z.B. Pfade/interne URLs sichtbar)
    DEBUG = 3   # Keine Maskierung (nur für lokale Entwicklung)


# Standardmäßig zu maskierende Schlüssel (Keys in Dicts)
DEFAULT_SENSITIVE_KEYS: Dict[SensitiveDataClass, Set[str]] = {
    SensitiveDataClass.TOKEN: {
        "token", "api_key", "access_token", "refresh_token", "agent_token",
        "registration_token", "bearer_token", "jwt_token"
    },
    SensitiveDataClass.SECRET: {
        "secret", "password", "new_password", "old_password", "secret_key",
        "mfa_encryption_key", "vault_token", "private_key", "ssh_key"
    },
    SensitiveDataClass.CREDENTIAL: {
        "authorization", "credentials", "auth", "proxy_auth", "basic_auth"
    },
    SensitiveDataClass.PATH: {
        "path", "file_path", "shell_path", "plugin_dirs", "vault_path",
        "abs_path", "relative_path", "mount_point", "log_file"
    },
    SensitiveDataClass.INTERNAL_URL: {
        "hub_url", "ollama_url", "lmstudio_url", "vault_url", "evolver_base_url",
        "openai_url", "anthropic_url", "mock_url", "agent_url", "controller_url"
    },
    SensitiveDataClass.PRIVATE_PROMPT: {
        "system_prompt", "private_prompt", "hidden_context", "instruction_override"
    },
    SensitiveDataClass.IP_ADDRESS: {
        "ip", "remote_addr", "server_ip", "client_ip"
    }
}


class Redactor:
    """Zentrale Schicht für die Maskierung sensibler Daten."""

    def __init__(self, default_visibility: VisibilityLevel = VisibilityLevel.USER):
        self.default_visibility = default_visibility
        # Kompilierte Regex-Muster für Performance
        self._patterns = self._compile_patterns()
        # Invertierter Index für schnelles Key-Mapping
        self._key_map = self._build_key_map()

    def _compile_patterns(self) -> Dict[SensitiveDataClass, re.Pattern]:
        # Generische Muster für String-Inhalte
        patterns = {
            SensitiveDataClass.TOKEN: re.compile(
                r"(?:api[_-]key|token|auth[_-]token)[=:]\s*([^\s,\)\"\']+)|(\bsk-[A-Za-z0-9_-]{20,})|(AKIA[0-9A-Z]{16})",
                re.IGNORECASE
            ),
            SensitiveDataClass.SECRET: re.compile(
                r"(?:password|secret|key)[=:]\s*([^\s,\)\"\']+)", re.IGNORECASE
            ),
            SensitiveDataClass.CREDENTIAL: re.compile(
                r"(?:authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)([^\s,\)\"\']+)", re.IGNORECASE
            ),
        }
        return patterns

    def _build_key_map(self) -> Dict[str, SensitiveDataClass]:
        key_map = {}
        for data_class, keys in DEFAULT_SENSITIVE_KEYS.items():
            for key in keys:
                key_map[key.lower()] = data_class
        return key_map

    def redact(self, data: Any, visibility: Optional[VisibilityLevel] = None) -> Any:
        """Maskiert sensible Daten in beliebigen Datenstrukturen."""
        current_vis = visibility if visibility is not None else self.default_visibility

        if current_vis >= VisibilityLevel.DEBUG:
            return data

        if isinstance(data, dict):
            return self._redact_dict(data, current_vis)
        elif isinstance(data, list):
            return [self.redact(item, current_vis) for item in data]
        elif isinstance(data, str):
            return self._redact_string(data, current_vis)
        elif hasattr(data, "model_dump"): # Pydantic v2
             return self._redact_dict(data.model_dump(), current_vis)
        elif hasattr(data, "dict"): # Pydantic v1
             return self._redact_dict(data.dict(), current_vis)

        return data

    def _redact_dict(self, data: Dict[str, Any], visibility: VisibilityLevel) -> Dict[str, Any]:
        redacted = {}
        for k, v in data.items():
            data_class = self._get_data_class_for_key(k)
            if data_class and self._should_redact(data_class, visibility):
                redacted[k] = f"***REDACTED_{data_class.upper()}***"
            elif isinstance(v, (dict, list, str)):
                redacted[k] = self.redact(v, visibility)
            elif hasattr(v, "model_dump") or hasattr(v, "dict"):
                redacted[k] = self.redact(v, visibility)
            else:
                redacted[k] = v
        return redacted

    def _redact_string(self, data: str, visibility: VisibilityLevel) -> str:
        redacted_str = data
        for data_class, pattern in self._patterns.items():
            if self._should_redact(data_class, visibility):
                # Wir ersetzen alle Treffer.
                # Falls eine Gruppe vorhanden ist, ersetzen wir nur diese, sonst den ganzen Match.
                def replace_match(m):
                    # Wenn Gruppe 1 (Key-Value Match) vorhanden
                    if m.groups() and m.group(1):
                        return m.group(0).replace(m.group(1), "***")
                    # Wenn es ein direkter Match ist (z.B. sk-...)
                    return "***"

                redacted_str = pattern.sub(replace_match, redacted_str)
        return redacted_str

    def _get_data_class_for_key(self, key: str) -> Optional[SensitiveDataClass]:
        return self._key_map.get(key.lower())

    def _should_redact(self, data_class: SensitiveDataClass, visibility: VisibilityLevel) -> bool:
        """Entscheidet basierend auf Datenklasse und Sichtbarkeit, ob maskiert wird."""
        if visibility >= VisibilityLevel.DEBUG:
            return False

        if visibility == VisibilityLevel.ADMIN:
            # Admins dürfen Pfade, interne URLs und IP-Adressen sehen
            if data_class in {
                SensitiveDataClass.PATH,
                SensitiveDataClass.INTERNAL_URL,
                SensitiveDataClass.IP_ADDRESS
            }:
                return False

        if visibility == VisibilityLevel.USER:
            # User dürfen evtl. IP-Adressen sehen (für Debugging), aber keine Pfade oder interne URLs
            if data_class == SensitiveDataClass.IP_ADDRESS:
                return False

        # Im Zweifel (PUBLIC oder andere Klassen) immer maskieren
        return True


# Default-Instanz
_redactor = Redactor()


def redact(data: Any, visibility: Optional[VisibilityLevel] = None) -> Any:
    """Komfort-Funktion für die zentrale Maskierung."""
    return _redactor.redact(data, visibility)
