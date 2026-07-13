"""Process-neutral sensitive-data redaction contract.

The implementation lives in the contracts package so Hub and Worker runtimes
can apply the same deterministic policy without crossing their process boundary.
"""

from __future__ import annotations

import enum
import re
from typing import Any


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
    PUBLIC = 0
    USER = 1
    ADMIN = 2
    DEBUG = 3


DEFAULT_SENSITIVE_KEYS: dict[SensitiveDataClass, set[str]] = {
    SensitiveDataClass.TOKEN: {
        "token",
        "api_key",
        "access_token",
        "refresh_token",
        "agent_token",
        "registration_token",
        "bearer_token",
        "jwt_token",
    },
    SensitiveDataClass.SECRET: {
        "secret",
        "password",
        "new_password",
        "old_password",
        "secret_key",
        "mfa_encryption_key",
        "vault_token",
        "private_key",
        "ssh_key",
    },
    SensitiveDataClass.CREDENTIAL: {
        "authorization",
        "credentials",
        "auth",
        "proxy_auth",
        "basic_auth",
    },
    SensitiveDataClass.PATH: {
        "path",
        "file_path",
        "shell_path",
        "plugin_dirs",
        "vault_path",
        "abs_path",
        "relative_path",
        "mount_point",
        "log_file",
    },
    SensitiveDataClass.INTERNAL_URL: {
        "hub_url",
        "ollama_url",
        "lmstudio_url",
        "vault_url",
        "evolver_base_url",
        "openai_url",
        "anthropic_url",
        "mock_url",
        "agent_url",
        "controller_url",
    },
    SensitiveDataClass.PRIVATE_PROMPT: {
        "system_prompt",
        "private_prompt",
        "hidden_context",
        "instruction_override",
    },
    SensitiveDataClass.IP_ADDRESS: {
        "ip",
        "remote_addr",
        "server_ip",
        "client_ip",
    },
}


class Redactor:
    """Apply one shared visibility policy to strings and nested values."""

    def __init__(self, default_visibility: VisibilityLevel = VisibilityLevel.USER) -> None:
        self.default_visibility = default_visibility
        self._patterns = self._compile_patterns()
        self._key_map = self._build_key_map()

    @staticmethod
    def _compile_patterns() -> dict[SensitiveDataClass, re.Pattern[str]]:
        return {
            SensitiveDataClass.TOKEN: re.compile(
                r"(?:api[_-]key|token|auth[_-]token)[=:\s]*([^\s,\)\"']+)"
                r"|(\bsk-[A-Za-z0-9_-]{20,})|(AKIA[0-9A-Z]{16})",
                re.IGNORECASE,
            ),
            SensitiveDataClass.SECRET: re.compile(
                r"(?:password|secret|key)[=:\s]*([^\s,\)\"']+)",
                re.IGNORECASE,
            ),
            SensitiveDataClass.CREDENTIAL: re.compile(
                r"(?:authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)"
                r"([^\s,\)\"']+)",
                re.IGNORECASE,
            ),
        }

    @staticmethod
    def _build_key_map() -> dict[str, SensitiveDataClass]:
        return {
            key.lower(): data_class
            for data_class, keys in DEFAULT_SENSITIVE_KEYS.items()
            for key in keys
        }

    def redact(
        self,
        data: Any,
        visibility: VisibilityLevel | None = None,
    ) -> Any:
        current_visibility = visibility if visibility is not None else self.default_visibility
        if current_visibility >= VisibilityLevel.DEBUG:
            return data
        if isinstance(data, dict):
            return self._redact_dict(data, current_visibility)
        if isinstance(data, list):
            return [self.redact(item, current_visibility) for item in data]
        if isinstance(data, str):
            return self._redact_string(data, current_visibility)
        if hasattr(data, "model_dump"):
            return self._redact_dict(data.model_dump(), current_visibility)
        if hasattr(data, "dict"):
            return self._redact_dict(data.dict(), current_visibility)
        return data

    def _redact_dict(
        self,
        data: dict[str, Any],
        visibility: VisibilityLevel,
    ) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            data_class = self._key_map.get(key.lower())
            if data_class and self._should_redact(data_class, visibility):
                redacted[key] = f"***REDACTED_{data_class.upper()}***"
            elif isinstance(value, (dict, list, str)) or hasattr(value, "model_dump") or hasattr(value, "dict"):
                redacted[key] = self.redact(value, visibility)
            else:
                redacted[key] = value
        return redacted

    def _redact_string(self, data: str, visibility: VisibilityLevel) -> str:
        redacted = data
        for data_class, pattern in self._patterns.items():
            if not self._should_redact(data_class, visibility):
                continue

            def replace_match(match: re.Match[str]) -> str:
                secret = match.group(1) if match.groups() else None
                return match.group(0).replace(secret, "***") if secret else "***"

            redacted = pattern.sub(replace_match, redacted)
        return redacted

    @staticmethod
    def _should_redact(
        data_class: SensitiveDataClass,
        visibility: VisibilityLevel,
    ) -> bool:
        if visibility >= VisibilityLevel.DEBUG:
            return False
        if visibility == VisibilityLevel.ADMIN and data_class in {
            SensitiveDataClass.PATH,
            SensitiveDataClass.INTERNAL_URL,
            SensitiveDataClass.IP_ADDRESS,
        }:
            return False
        if visibility == VisibilityLevel.USER and data_class == SensitiveDataClass.IP_ADDRESS:
            return False
        return True


_redactor = Redactor()


def redact(data: Any, visibility: VisibilityLevel | None = None) -> Any:
    """Redact a value with the shared default policy."""

    return _redactor.redact(data, visibility)


__all__ = [
    "DEFAULT_SENSITIVE_KEYS",
    "Redactor",
    "SensitiveDataClass",
    "VisibilityLevel",
    "redact",
]
