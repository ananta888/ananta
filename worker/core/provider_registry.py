"""WorkerProviderRegistry: provider management, selection policy, credential isolation,
auxiliary model policy, and runtime diagnostics.

EW-T020: WorkerProviderRegistry with all provider types.
EW-T021: Provider selection only via ExecutionEnvelope (Hub decision).
EW-T022: Local-first routing with health checks.
EW-T023: Credential isolation — keys scoped to provider.
EW-T024: Auxiliary model policy.
EW-T025: Provider diagnostics endpoint (never returns secrets).
AWF-T012+T013: ProviderSelectionGate — Hub-driven provider selection from ModelPolicy.
AWF-T014: CredentialIsolationProof — verifies subprocess env is credential-clean.
AWF-T015: ProviderHealthGate — health check before dispatch.
AWF-T016: ProviderProvenanceRef — tracks provider in all model-generated artifacts.
"""
from __future__ import annotations

import hashlib
import json
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


# ── ProviderSelectionGate (AWF-T012, AWF-T013) ───────────────────────────────

@dataclass(frozen=True)
class ModelPolicy:
    """Portable model policy — mirrors ExecutionEnvelope.ModelPolicy for standalone use."""
    cloud_allowed: bool = False
    allowed_providers: list[str] = field(default_factory=list)
    preferred_model: str | None = None

    def is_cloud_allowed(self) -> bool:
        return self.cloud_allowed

    def is_provider_allowed(self, provider_id: str) -> bool:
        if not self.allowed_providers:
            return True
        return provider_id.lower() in {p.lower() for p in self.allowed_providers}


class ProviderSelectionGate:
    """Selects a provider from the registry based on Hub-issued ModelPolicy. AWF-T012, T013.

    Worker NEVER autonomously selects cloud providers — all selection is
    driven by the policy from the ExecutionEnvelope.
    """

    def __init__(self, registry: WorkerProviderRegistry) -> None:
        self._registry = registry

    def select(
        self,
        *,
        policy: ModelPolicy,
        preferred_provider: str | None = None,
    ) -> tuple[ProviderEntry | None, str]:
        """Return (entry, reason). reason='allow' or a denial code. AWF-T012/T013."""
        candidate_id = preferred_provider or ""

        # Try the preferred provider first
        if candidate_id:
            entry, reason = self._registry.resolve_provider(
                candidate_id,
                cloud_allowed=policy.cloud_allowed,
                allowed_providers=policy.allowed_providers or None,
            )
            if entry is not None:
                return entry, "allow"

        # Local-first fallback (AWF-T012: registry covers local + cloud)
        fallback = self._registry.select_local_fallback(
            preferred=candidate_id or None,
            allowed_providers=policy.allowed_providers or None,
        )
        if fallback is not None:
            return fallback, "allow:local_fallback"

        # If cloud is allowed, try any cloud provider
        if policy.cloud_allowed:
            for entry in sorted(self._registry._providers.values(), key=lambda e: e.priority):
                if entry.kind == ProviderKind.cloud:
                    allowed = not policy.allowed_providers or entry.id.lower() in {p.lower() for p in policy.allowed_providers}
                    if allowed:
                        return entry, "allow:cloud_fallback"

        return None, "no_provider_available"

    def select_from_envelope(
        self,
        *,
        cloud_allowed: bool,
        allowed_providers: list[str],
        preferred_provider: str | None = None,
    ) -> tuple[ProviderEntry | None, str]:
        """Convenience wrapper accepting raw envelope fields. AWF-T013."""
        return self.select(
            policy=ModelPolicy(cloud_allowed=cloud_allowed, allowed_providers=allowed_providers),
            preferred_provider=preferred_provider,
        )


# ── CredentialIsolationProof (AWF-T014) ───────────────────────────────────────

_KNOWN_CREDENTIAL_ENV_VARS: frozenset[str] = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY", "HF_TOKEN", "GITHUB_TOKEN", "COHERE_API_KEY",
})


@dataclass(frozen=True)
class CredentialIsolationProof:
    """Attestation that a subprocess env contains no leaked credentials. AWF-T014."""
    provider_id: str
    credential_vars_present: frozenset[str]
    credential_vars_leaked: frozenset[str]
    is_isolated: bool

    @classmethod
    def verify(cls, provider_id: str, env: dict[str, str]) -> "CredentialIsolationProof":
        present = frozenset(k for k in env if k in _KNOWN_CREDENTIAL_ENV_VARS)
        # Leaked = credential vars that belong to OTHER providers (not the scoped one)
        scoped_var = _provider_env_var(provider_id)
        leaked = frozenset(k for k in present if k != scoped_var)
        return cls(
            provider_id=provider_id,
            credential_vars_present=present,
            credential_vars_leaked=leaked,
            is_isolated=len(leaked) == 0,
        )


# ── ProviderHealthGate (AWF-T015) ─────────────────────────────────────────────

class ProviderHealthGate:
    """Lightweight availability check before provider dispatch. AWF-T015.

    Checks the registry for a last-known diagnostic; records a synthetic
    'unavailable' diagnostic when provider is blocked by policy.
    Does NOT make network calls — callers own the actual probe.
    """

    def check_from_registry(
        self,
        provider_id: str,
        registry: WorkerProviderRegistry,
    ) -> tuple[bool, str]:
        """Return (healthy, reason). Uses last recorded diagnostic if available."""
        entry = registry._providers.get(provider_id.lower())
        if entry is None:
            return False, "provider_not_registered"

        diag = registry._diagnostics.get(provider_id.lower())
        if diag is None:
            # No diagnostic yet — assume available for local/mock providers, unknown for cloud
            if entry.kind == ProviderKind.cloud:
                return True, "assume_available:cloud_unprobed"
            return True, "assume_available:local"

        if diag.status in (ProviderStatus.unavailable, ProviderStatus.unauthorized, ProviderStatus.timeout):
            return False, f"provider_{diag.status.value}"
        if diag.status == ProviderStatus.blocked_by_policy:
            return False, "provider_blocked_by_policy"
        return True, diag.status.value

    def record_failure(
        self,
        provider_id: str,
        registry: WorkerProviderRegistry,
        *,
        status: ProviderStatus = ProviderStatus.unavailable,
        error_detail: str = "",
        latency_ms: float | None = None,
    ) -> ProviderDiagnostic:
        """Record a health failure in the registry. AWF-T015."""
        entry = registry._providers.get(provider_id.lower())
        kind = entry.kind if entry else ProviderKind.unknown
        diag = ProviderDiagnostic(
            provider_id=provider_id,
            status=status,
            kind=kind,
            error_detail=error_detail,
            latency_ms=latency_ms,
        )
        registry.record_diagnostic(diag)
        return diag

    def record_success(
        self,
        provider_id: str,
        registry: WorkerProviderRegistry,
        *,
        latency_ms: float | None = None,
        model_count: int | None = None,
    ) -> ProviderDiagnostic:
        """Record a successful health check. AWF-T015."""
        entry = registry._providers.get(provider_id.lower())
        kind = entry.kind if entry else ProviderKind.unknown
        diag = ProviderDiagnostic(
            provider_id=provider_id,
            status=ProviderStatus.available,
            kind=kind,
            latency_ms=latency_ms,
            model_count=model_count,
        )
        registry.record_diagnostic(diag)
        return diag


# ── ProviderProvenanceRef (AWF-T016) ─────────────────────────────────────────

@dataclass(frozen=True)
class ProviderProvenanceRef:
    """Attestation of which provider produced a model-generated artifact. AWF-T016.

    Included in all artifacts produced by model/provider calls so the Hub
    can audit the chain: task → envelope → provider → artifact.
    """
    provider_id: str
    model_id: str
    base_url: str = ""
    entry_hash: str = ""        # sha256 of safe_info() at time of call

    @classmethod
    def from_entry(cls, entry: ProviderEntry, *, model_id: str) -> "ProviderProvenanceRef":
        raw = json.dumps(entry.safe_info(), sort_keys=True, separators=(",", ":"))
        entry_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return cls(
            provider_id=entry.id,
            model_id=model_id,
            base_url=entry.base_url,
            entry_hash=entry_hash,
        )

    @classmethod
    def native_worker(cls) -> "ProviderProvenanceRef":
        """Provenance ref for the native worker (no external model call)."""
        return cls(
            provider_id="native_worker",
            model_id="native_command_runtime",
            base_url="",
            entry_hash="native",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "entry_hash": self.entry_hash,
        }


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
