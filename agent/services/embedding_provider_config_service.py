"""
EmbeddingProviderConfig and EmbeddingProviderConfigService.
Implements EPC-002, EPC-003, EPC-004, EPC-006.

Security contract (EPC-003):
  - Default provider is local_hash — offline, deterministic, no network.
  - external_calls_allowed is False by default.
  - OpenAI-compatible provider is only built when external_calls_allowed=True
    AND base_url is in allowed_base_urls (if that list is non-empty).
  - api_key is never serialised; diagnostics receive a redacted placeholder.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

ALLOWED_PROVIDERS = frozenset({
    "fake", "test", "local", "local_hash", "hash",
    "openai", "openai_compatible",
})

VALID_SCOPES = frozenset({
    "worker_retrieval",
    "codecompass_vector",
    "semantic_output_correction",
    "rag_helper",
})

ProviderStatus = Literal["ready", "degraded", "blocked"]


@dataclass
class EmbeddingProviderConfig:
    """
    Canonical config for a single embedding provider scope (EPC-002).
    Matches the proposed_config_shape in the EPC todo.
    """
    provider: str = "local_hash"
    model: str | None = None
    model_version: str = "hash-v1"
    dimensions: int = 12
    base_url: str | None = None
    api_key_ref: str | None = None        # reference only — never a plaintext secret
    timeout_seconds: int = 20
    external_calls_allowed: bool = False   # default deny (EPC-003)
    allowed_base_urls: list[str] = field(default_factory=list)
    index_rebuild_policy: str = "on_provider_or_model_change"
    diagnostics_enabled: bool = True
    scope: str = "worker_retrieval"

    # resolved at runtime, not persisted
    _resolved_api_key: str | None = field(default=None, repr=False, compare=False)

    def as_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "dimensions": self.dimensions,
            "base_url": self.base_url,
            "api_key_ref": self.api_key_ref,
            "api_key": "[REDACTED]" if redact_secrets else (self._resolved_api_key or ""),
            "timeout_seconds": self.timeout_seconds,
            "external_calls_allowed": self.external_calls_allowed,
            "allowed_base_urls": list(self.allowed_base_urls),
            "index_rebuild_policy": self.index_rebuild_policy,
            "diagnostics_enabled": self.diagnostics_enabled,
            "scope": self.scope,
        }

    def config_hash(self) -> str:
        """Stable hash of provider identity — excludes secrets. Used for rebuild detection."""
        identity = json.dumps({
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "dimensions": self.dimensions,
            "base_url": self.base_url,
            "scope": self.scope,
        }, sort_keys=True)
        return hashlib.sha256(identity.encode()).hexdigest()[:16]


@dataclass
class EmbeddingProviderDiagnostic:
    scope: str
    status: ProviderStatus
    provider: str
    reason: str = ""
    config_hash: str = ""
    dimensions: int = 0


class EmbeddingProviderConfigService:
    """
    Central resolver for EmbeddingProviderConfig across all scopes (EPC-004).

    Merges:
      - A global default config
      - Per-scope overrides (semantic_output_correction, codecompass_vector, …)
    """

    # Absolute safe defaults — offline, deterministic (EPC-003)
    _DEFAULT_CONFIG: dict[str, Any] = {
        "provider": "local_hash",
        "model_version": "hash-v1",
        "dimensions": 12,
        "external_calls_allowed": False,
    }

    def __init__(
        self,
        global_config: dict[str, Any] | None = None,
        scope_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._global: dict[str, Any] = {**self._DEFAULT_CONFIG, **(global_config or {})}
        self._overrides: dict[str, dict[str, Any]] = dict(scope_overrides or {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, scope: str = "worker_retrieval") -> EmbeddingProviderConfig:
        """Return the effective config for *scope*, merging global + overrides."""
        base = dict(self._global)
        override = self._overrides.get(scope, {})
        merged = {**base, **override, "scope": scope}
        return self._normalise(merged)

    def resolve_for_build(self, scope: str = "worker_retrieval") -> dict[str, Any]:
        """Return a flat dict suitable for passing to build_embedding_provider()."""
        cfg = self.resolve(scope)
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "model_version": cfg.model_version,
            "dimensions": cfg.dimensions,
            "base_url": cfg.base_url,
            "api_key": cfg._resolved_api_key,
            "timeout_seconds": cfg.timeout_seconds,
            "external_calls_allowed": cfg.external_calls_allowed,
            "allowed_base_urls": cfg.allowed_base_urls,
        }

    def diagnostic(self, scope: str = "worker_retrieval") -> EmbeddingProviderDiagnostic:
        try:
            cfg = self.resolve(scope)
        except Exception as exc:
            return EmbeddingProviderDiagnostic(
                scope=scope,
                status="blocked",
                provider="unknown",
                reason=str(exc),
            )
        status, reason = self._check_security(cfg)
        return EmbeddingProviderDiagnostic(
            scope=scope,
            status=status,
            provider=cfg.provider,
            reason=reason,
            config_hash=cfg.config_hash(),
            dimensions=cfg.dimensions,
        )

    def all_diagnostics(self) -> list[EmbeddingProviderDiagnostic]:
        scopes = set(VALID_SCOPES) | set(self._overrides.keys())
        return [self.diagnostic(s) for s in sorted(scopes)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(raw: dict[str, Any]) -> EmbeddingProviderConfig:
        provider = str(raw.get("provider") or "local_hash").strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            raise ValueError(f"unknown_embedding_provider:{provider!r}")

        dims_raw = raw.get("dimensions")
        try:
            dims = max(1, int(dims_raw)) if dims_raw is not None else 12
        except (TypeError, ValueError):
            dims = 12

        timeout_raw = raw.get("timeout_seconds")
        try:
            timeout = max(1, int(timeout_raw)) if timeout_raw is not None else 20
        except (TypeError, ValueError):
            timeout = 20

        allowed_urls_raw = raw.get("allowed_base_urls") or []
        allowed_urls = [str(u).strip() for u in allowed_urls_raw if str(u or "").strip()]

        cfg = EmbeddingProviderConfig(
            provider=provider,
            model=str(raw["model"]).strip() or None if raw.get("model") else None,
            model_version=str(raw.get("model_version") or "hash-v1"),
            dimensions=dims,
            base_url=str(raw["base_url"]).strip() or None if raw.get("base_url") else None,
            api_key_ref=str(raw.get("api_key_ref") or "").strip() or None,
            timeout_seconds=timeout,
            external_calls_allowed=bool(raw.get("external_calls_allowed", False)),
            allowed_base_urls=allowed_urls,
            index_rebuild_policy=str(raw.get("index_rebuild_policy") or "on_provider_or_model_change"),
            diagnostics_enabled=bool(raw.get("diagnostics_enabled", True)),
            scope=str(raw.get("scope") or "worker_retrieval"),
        )
        # Resolve api_key for runtime use — never stored in the Config object itself
        raw_key = str(raw.get("api_key") or "").strip()
        if raw_key:
            object.__setattr__(cfg, "_resolved_api_key", raw_key)
        return cfg

    @staticmethod
    def _base_url_allowed(base_url: str, allowed_base_urls: list[str]) -> bool:
        """Allow exact URL origins, with optional path prefix bounded on path segments."""
        candidate = urlparse(str(base_url or "").rstrip("/"))
        if not candidate.scheme or not candidate.netloc:
            return False
        candidate_path = (candidate.path or "").rstrip("/")
        for raw_allowed in allowed_base_urls:
            allowed = urlparse(str(raw_allowed or "").rstrip("/"))
            if not allowed.scheme or not allowed.netloc:
                continue
            if candidate.scheme.lower() != allowed.scheme.lower():
                continue
            if candidate.hostname != allowed.hostname:
                continue
            if (candidate.port or _default_url_port(candidate.scheme)) != (
                allowed.port or _default_url_port(allowed.scheme)
            ):
                continue
            allowed_path = (allowed.path or "").rstrip("/")
            if not allowed_path:
                return True
            if candidate_path == allowed_path or candidate_path.startswith(f"{allowed_path}/"):
                return True
        return False

    @staticmethod
    def _check_security(cfg: EmbeddingProviderConfig) -> tuple[ProviderStatus, str]:
        """Return (status, reason) for diagnostics."""
        is_external = cfg.provider in {"openai", "openai_compatible"}
        if is_external and not cfg.external_calls_allowed:
            return "blocked", "external_calls_not_allowed"
        if is_external and cfg.allowed_base_urls and cfg.base_url:
            if not EmbeddingProviderConfigService._base_url_allowed(
                cfg.base_url,
                cfg.allowed_base_urls,
            ):
                return "blocked", "base_url_not_in_allowed_list"
        if is_external and not cfg.base_url:
            return "degraded", "missing_base_url"
        if cfg.provider in {"fake", "test"}:
            return "degraded", "fake_provider_not_for_production"
        return "ready", ""


def _default_url_port(scheme: str) -> int | None:
    if scheme.lower() == "http":
        return 80
    if scheme.lower() == "https":
        return 443
    return None


# ---------------------------------------------------------------------------
# build_embedding_provider guard (EPC-006)
# ---------------------------------------------------------------------------

def build_embedding_provider_from_config(
    config: "EmbeddingProviderConfig",
) -> Any:
    """
    Build a provider from a fully validated EmbeddingProviderConfig.
    Enforces the external_calls_allowed policy before constructing external providers.
    Raises ValueError for policy violations so callers can degrade gracefully.
    """
    from worker.retrieval.embedding_provider import (
        build_embedding_provider,
        EmbeddingProviderError,
    )

    status, reason = EmbeddingProviderConfigService._check_security(config)
    if status == "blocked":
        raise ValueError(f"embedding_provider_blocked:{reason}")

    build_dict = {
        "provider": config.provider,
        "model_version": config.model_version,
        "dimensions": config.dimensions,
        "timeout_seconds": config.timeout_seconds,
    }
    if config.model:
        build_dict["model"] = config.model
    if config.base_url:
        build_dict["base_url"] = config.base_url
    if config._resolved_api_key:
        build_dict["api_key"] = config._resolved_api_key

    return build_embedding_provider(build_dict)
