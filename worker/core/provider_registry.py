"""WorkerProviderRegistry: provider management, selection policy, credential isolation,
auxiliary model policy, and runtime diagnostics.

EW-T020: WorkerProviderRegistry with all provider types.
EW-T021: Provider selection only via ExecutionEnvelope (Hub decision).
EW-T022: Local-first routing with health checks.
EW-T023: Credential isolation — keys scoped to provider.
EW-T024: Auxiliary model policy.
EW-T025: Provider diagnostics endpoint (never returns secrets).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Provider types ────────────────────────────────────────────────────────────

class ProviderKind(str, Enum):
    local = "local"
    cloud = "cloud"
    local_mock = "local_mock"


class ProviderStatus(str, Enum):
    available = "available"
    unavailable = "unavailable"
    unauthorized = "unauthorized"
    misconfigured = "misconfigured"
    timeout = "timeout"
    blocked_by_policy = "blocked_by_policy"
    unknown = "unknown"


CLOUD_PROVIDERS = frozenset({
    "openai", "anthropic", "gemini", "groq", "openrouter", "bedrock", "azure",
})

LOCAL_PROVIDERS = frozenset({
    "ollama", "lmstudio", "llamacpp", "textgen_webui", "koboldcpp",
    "openai_compatible", "local_mock",
})


# ── AuxiliaryTaskKind ─────────────────────────────────────────────────────────

class AuxiliaryTaskKind(str, Enum):
    compression = "compression"
    summarization = "summarization"
    validation = "validation"
    search_summary = "search_summary"
    skill_review = "skill_review"
    diff_description = "diff_description"


# ── Credential store ──────────────────────────────────────────────────────────

class CredentialStore:
    """Scopes API keys to (provider_id, base_url). EW-T023.

    Keys are never shared across providers and never exposed to subprocesses
    unless explicitly injected into a scoped env.
    """

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], str] = {}

    def set(self, provider_id: str, base_url: str, api_key: str) -> None:
        self._keys[(provider_id.lower(), base_url.lower())] = api_key

    def get(self, provider_id: str, base_url: str) -> str | None:
        return self._keys.get((provider_id.lower(), base_url.lower()))

    def scoped_env(self, provider_id: str, base_url: str) -> dict[str, str]:
        """Return a minimal env dict for a subprocess — only this provider's key."""
        key = self.get(provider_id, base_url)
        if key is None:
            return {}
        env_var = _provider_env_var(provider_id)
        return {env_var: key} if env_var else {}

    def env_for_subprocess(
        self,
        provider_id: str,
        base_url: str,
        *,
        inherit_env: bool = False,
    ) -> dict[str, str]:
        """Build subprocess env. By default does NOT inherit parent process env. EW-T023."""
        base: dict[str, str] = {}
        if inherit_env:
            # Strip all known provider key env vars before injecting the scoped one
            base = {k: v for k, v in os.environ.items() if not _is_provider_key_var(k)}
        scoped = self.scoped_env(provider_id, base_url)
        base.update(scoped)
        return base


def _provider_env_var(provider_id: str) -> str:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",
        "azure": "AZURE_OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
    }
    return mapping.get(provider_id.lower(), "")


_ALL_PROVIDER_KEY_VARS = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY", "HF_TOKEN", "GITHUB_TOKEN",
})


def _is_provider_key_var(name: str) -> bool:
    return name.upper() in _ALL_PROVIDER_KEY_VARS


# ── ProviderEntry ─────────────────────────────────────────────────────────────

@dataclass
class ProviderEntry:
    id: str
    kind: ProviderKind
    base_url: str = ""
    supports_tools: bool = False
    supports_streaming: bool = False
    default_model: str | None = None
    credential_source: str = ""  # e.g. "env:OPENAI_API_KEY", "keychain:provider_id"
    priority: int = 100          # lower = higher priority; local providers get lower numbers

    def safe_info(self) -> dict[str, Any]:
        """Safe representation — never includes credentials or raw secrets. EW-T025."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "base_url": self.base_url,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "default_model": self.default_model,
            "priority": self.priority,
        }


# ── AuxiliaryPolicy ───────────────────────────────────────────────────────────

@dataclass
class AuxiliaryPolicy:
    """Per-task-kind policy for auxiliary (non-primary) model calls. EW-T024."""
    auxiliary_allowed_providers: list[str] = field(default_factory=list)
    cloud_allowed_for_auxiliary: bool = False

    def is_allowed(
        self,
        provider_id: str,
        task_kind: AuxiliaryTaskKind,
        *,
        context_is_sensitive: bool = False,
    ) -> tuple[bool, str]:
        """Returns (allowed, reason). Cloud + sensitive context → blocked."""
        pid = provider_id.lower()

        # Block cloud if cloud not allowed
        if pid in CLOUD_PROVIDERS and not self.cloud_allowed_for_auxiliary:
            return False, "provider_blocked"

        # Block cloud + sensitive context regardless of flag
        if pid in CLOUD_PROVIDERS and context_is_sensitive:
            return False, "context_sensitivity_blocked"

        # If allowlist is set, enforce it
        if self.auxiliary_allowed_providers:
            if pid not in [p.lower() for p in self.auxiliary_allowed_providers]:
                return False, "provider_blocked"

        return True, "auxiliary_allow"


# ── ProviderDiagnostics ───────────────────────────────────────────────────────

@dataclass
class ProviderDiagnostic:
    provider_id: str
    status: ProviderStatus
    kind: ProviderKind
    model_count: int | None = None
    error_detail: str = ""
    latency_ms: float | None = None
    checked_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Never returns secrets, raw headers, or env vars. EW-T025."""
        return {
            "provider_id": self.provider_id,
            "status": self.status.value,
            "kind": self.kind.value,
            "model_count": self.model_count,
            "error_detail": self.error_detail,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
        }


# ── WorkerProviderRegistry ────────────────────────────────────────────────────

class WorkerProviderRegistry:
    """Registry of all model providers available to this worker.

    Hub selects the provider via ExecutionEnvelope — worker does not select
    cloud providers autonomously. EW-T021.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderEntry] = {}
        self._credentials = CredentialStore()
        self._diagnostics: dict[str, ProviderDiagnostic] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, entry: ProviderEntry) -> None:
        self._providers[entry.id.lower()] = entry

    def register_credential(self, provider_id: str, base_url: str, api_key: str) -> None:
        """Store credential scoped to this provider. EW-T023."""
        self._credentials.set(provider_id, base_url, api_key)

    # ── Selection (Hub-driven) ────────────────────────────────────────────────

    def resolve_provider(
        self,
        provider_id: str,
        *,
        cloud_allowed: bool = False,
        allowed_providers: list[str] | None = None,
    ) -> tuple[ProviderEntry | None, str]:
        """Resolve provider by id, enforcing cloud policy. EW-T021.

        Returns (entry, reason_code). reason_code is "allow" or a denial code.
        Worker NEVER autonomously selects a cloud provider.
        """
        entry = self._providers.get(provider_id.lower())
        if entry is None:
            return None, "provider_unavailable"

        if entry.kind == ProviderKind.cloud and not cloud_allowed:
            return None, "provider_blocked"

        if allowed_providers:
            if provider_id.lower() not in [p.lower() for p in allowed_providers]:
                return None, "provider_blocked"

        return entry, "allow"

    def local_providers_by_priority(self) -> list[ProviderEntry]:
        """Sorted list of local providers — for local-first fallback routing. EW-T022."""
        return sorted(
            [p for p in self._providers.values() if p.kind == ProviderKind.local],
            key=lambda e: e.priority,
        )

    def select_local_fallback(
        self,
        preferred: str | None,
        *,
        allowed_providers: list[str] | None = None,
    ) -> ProviderEntry | None:
        """Return the best available local provider. EW-T022."""
        candidates = self.local_providers_by_priority()
        if allowed_providers:
            allowed_lower = {p.lower() for p in allowed_providers}
            candidates = [c for c in candidates if c.id.lower() in allowed_lower]
        if preferred:
            for c in candidates:
                if c.id.lower() == preferred.lower():
                    return c
        return candidates[0] if candidates else None

    # ── Credentials ───────────────────────────────────────────────────────────

    def subprocess_env(
        self,
        provider_id: str,
        *,
        inherit_env: bool = False,
    ) -> dict[str, str]:
        """Scoped subprocess env for this provider. EW-T023."""
        entry = self._providers.get(provider_id.lower())
        base_url = entry.base_url if entry else ""
        return self._credentials.env_for_subprocess(
            provider_id, base_url, inherit_env=inherit_env
        )

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def record_diagnostic(self, diag: ProviderDiagnostic) -> None:
        self._diagnostics[diag.provider_id.lower()] = diag

    def diagnostics(self) -> list[dict[str, Any]]:
        """All provider diagnostics — safe to return to Hub. EW-T025."""
        return [d.as_dict() for d in self._diagnostics.values()]

    def provider_info(self) -> list[dict[str, Any]]:
        """Provider catalog — safe (no secrets). EW-T025."""
        return [p.safe_info() for p in sorted(self._providers.values(), key=lambda e: e.id)]


# ── Default registry ──────────────────────────────────────────────────────────

def build_default_provider_registry() -> WorkerProviderRegistry:
    registry = WorkerProviderRegistry()
    registry.register(ProviderEntry(
        id="ollama",
        kind=ProviderKind.local,
        base_url="http://localhost:11434",
        supports_streaming=True,
        priority=10,
    ))
    registry.register(ProviderEntry(
        id="lmstudio",
        kind=ProviderKind.local,
        base_url="http://localhost:1234/v1",
        supports_tools=True,
        supports_streaming=True,
        priority=20,
    ))
    registry.register(ProviderEntry(
        id="openai_compatible",
        kind=ProviderKind.local,
        base_url="http://localhost:8080/v1",
        supports_tools=True,
        priority=30,
    ))
    registry.register(ProviderEntry(
        id="local_mock",
        kind=ProviderKind.local_mock,
        priority=99,
        supports_tools=True,
    ))
    for cloud_id in ["openai", "anthropic", "gemini", "groq", "openrouter"]:
        registry.register(ProviderEntry(
            id=cloud_id,
            kind=ProviderKind.cloud,
            supports_tools=True,
            supports_streaming=True,
            priority=200,
        ))
    return registry
