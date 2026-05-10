"""Tests for worker/core/provider_registry.py (EW-T020 through EW-T025)."""
import pytest

from worker.core.provider_registry import (
    AuxiliaryPolicy,
    AuxiliaryTaskKind,
    CredentialStore,
    ProviderDiagnostic,
    ProviderEntry,
    ProviderKind,
    ProviderStatus,
    WorkerProviderRegistry,
    build_default_provider_registry,
    _is_provider_key_var,
)


# ── EW-T020: WorkerProviderRegistry ──────────────────────────────────────────

class TestWorkerProviderRegistry:
    def setup_method(self):
        self.registry = WorkerProviderRegistry()
        self.registry.register(ProviderEntry(id="ollama", kind=ProviderKind.local,
            base_url="http://localhost:11434", priority=10))
        self.registry.register(ProviderEntry(id="openai", kind=ProviderKind.cloud,
            base_url="https://api.openai.com/v1"))

    def test_register_and_resolve_local(self):
        entry, reason = self.registry.resolve_provider("ollama", cloud_allowed=False)
        assert entry is not None and entry.id == "ollama" and reason == "allow"

    def test_resolve_unknown_returns_unavailable(self):
        entry, reason = self.registry.resolve_provider("nonexistent", cloud_allowed=False)
        assert entry is None and reason == "provider_unavailable"

    def test_provider_info_no_secrets(self):
        for p in self.registry.provider_info():
            assert "api_key" not in str(p).lower()

    def test_default_registry_has_standard_providers(self):
        registry = build_default_provider_registry()
        for pid in ["ollama", "lmstudio", "openai", "anthropic", "local_mock"]:
            entry, _ = registry.resolve_provider(pid, cloud_allowed=True)
            assert entry is not None, f"{pid} missing"


# ── EW-T021: Hub-driven provider selection ────────────────────────────────────

class TestProviderSelectionPolicy:
    def setup_method(self):
        self.registry = build_default_provider_registry()

    def test_cloud_blocked_when_not_allowed(self):
        for pid in ["openai", "anthropic", "groq"]:
            entry, reason = self.registry.resolve_provider(pid, cloud_allowed=False)
            assert entry is None and reason == "provider_blocked"

    def test_cloud_allowed_when_permitted(self):
        entry, reason = self.registry.resolve_provider("openai", cloud_allowed=True)
        assert entry is not None and reason == "allow"

    def test_allowlist_restricts(self):
        entry, reason = self.registry.resolve_provider(
            "lmstudio", cloud_allowed=False, allowed_providers=["ollama"])
        assert entry is None and reason == "provider_blocked"

    def test_allowlist_permits_listed(self):
        entry, _ = self.registry.resolve_provider(
            "ollama", cloud_allowed=False, allowed_providers=["ollama", "lmstudio"])
        assert entry is not None


# ── EW-T022: Local-first routing ─────────────────────────────────────────────

class TestLocalFirstRouting:
    def setup_method(self):
        self.registry = build_default_provider_registry()

    def test_local_providers_sorted_by_priority(self):
        providers = self.registry.local_providers_by_priority()
        assert all(p.kind == ProviderKind.local for p in providers)
        priorities = [p.priority for p in providers]
        assert priorities == sorted(priorities)

    def test_fallback_returns_highest_priority(self):
        fallback = self.registry.select_local_fallback(preferred=None)
        assert fallback is not None and fallback.kind == ProviderKind.local

    def test_fallback_respects_preferred(self):
        fallback = self.registry.select_local_fallback(preferred="lmstudio")
        assert fallback is not None and fallback.id == "lmstudio"

    def test_fallback_with_allowlist(self):
        fallback = self.registry.select_local_fallback(
            preferred=None, allowed_providers=["lmstudio"])
        assert fallback is not None and fallback.id == "lmstudio"

    def test_fallback_none_when_allowlist_excludes_all(self):
        fallback = self.registry.select_local_fallback(
            preferred=None, allowed_providers=["openai"])
        assert fallback is None


# ── EW-T023: Credential isolation ────────────────────────────────────────────

class TestCredentialIsolation:
    def setup_method(self):
        self.store = CredentialStore()

    def test_key_scoped_to_provider(self):
        self.store.set("openai", "https://api.openai.com", "sk-s1")
        self.store.set("anthropic", "https://api.anthropic.com", "sk-ant-s2")
        assert self.store.get("openai", "https://api.openai.com") == "sk-s1"
        assert self.store.get("anthropic", "https://api.anthropic.com") == "sk-ant-s2"

    def test_openai_key_not_leaked_to_other(self):
        self.store.set("openai", "https://api.openai.com", "sk-openai-secret")
        assert self.store.get("anthropic", "https://api.anthropic.com") is None

    def test_scoped_env_only_contains_this_providers_key(self):
        self.store.set("openai", "https://api.openai.com/v1", "sk-mykey")
        env = self.store.scoped_env("openai", "https://api.openai.com/v1")
        assert "OPENAI_API_KEY" in env and env["OPENAI_API_KEY"] == "sk-mykey"
        assert "ANTHROPIC_API_KEY" not in env

    def test_subprocess_env_no_inherit_is_dict(self):
        env = self.store.env_for_subprocess("ollama", base_url="", inherit_env=False)
        assert isinstance(env, dict)

    def test_provider_key_var_detection(self):
        assert _is_provider_key_var("OPENAI_API_KEY") is True
        assert _is_provider_key_var("PATH") is False

    def test_registry_subprocess_env(self):
        registry = WorkerProviderRegistry()
        registry.register(ProviderEntry(
            id="openai", kind=ProviderKind.cloud, base_url="https://api.openai.com/v1"))
        registry.register_credential("openai", "https://api.openai.com/v1", "sk-testkey")
        env = registry.subprocess_env("openai")
        assert env.get("OPENAI_API_KEY") == "sk-testkey"


# ── EW-T024: Auxiliary model policy ──────────────────────────────────────────

class TestAuxiliaryPolicy:
    def setup_method(self):
        self.policy = AuxiliaryPolicy(
            auxiliary_allowed_providers=["ollama", "lmstudio"],
            cloud_allowed_for_auxiliary=False,
        )

    def test_allowed_local_passes(self):
        ok, _ = self.policy.is_allowed("ollama", AuxiliaryTaskKind.compression)
        assert ok is True

    def test_cloud_blocked(self):
        ok, reason = self.policy.is_allowed("openai", AuxiliaryTaskKind.summarization)
        assert ok is False and reason == "provider_blocked"

    def test_cloud_with_sensitive_context_blocked(self):
        policy = AuxiliaryPolicy(cloud_allowed_for_auxiliary=True)
        ok, reason = policy.is_allowed(
            "openai", AuxiliaryTaskKind.summarization, context_is_sensitive=True)
        assert ok is False and reason == "context_sensitivity_blocked"

    def test_not_in_allowlist_blocked(self):
        ok, reason = self.policy.is_allowed("groq", AuxiliaryTaskKind.validation)
        assert ok is False and reason == "provider_blocked"

    def test_empty_allowlist_allows_local(self):
        policy = AuxiliaryPolicy(auxiliary_allowed_providers=[], cloud_allowed_for_auxiliary=False)
        ok, _ = policy.is_allowed("lmstudio", AuxiliaryTaskKind.diff_description)
        assert ok is True

    def test_all_kinds_accepted(self):
        policy = AuxiliaryPolicy(cloud_allowed_for_auxiliary=True)
        for kind in AuxiliaryTaskKind:
            ok, _ = policy.is_allowed("openai", kind)
            assert isinstance(ok, bool)


# ── EW-T025: Provider diagnostics ────────────────────────────────────────────

class TestProviderDiagnostics:
    def setup_method(self):
        self.registry = WorkerProviderRegistry()
        self.registry.register(ProviderEntry(
            id="ollama", kind=ProviderKind.local, base_url="http://localhost:11434"))

    def test_diagnostics_no_secrets(self):
        self.registry.record_diagnostic(ProviderDiagnostic(
            provider_id="ollama", status=ProviderStatus.available,
            kind=ProviderKind.local, model_count=5))
        for entry in self.registry.diagnostics():
            assert "api_key" not in str(entry).lower()

    def test_all_status_values_representable(self):
        for status in ProviderStatus:
            d = ProviderDiagnostic(
                provider_id=f"p-{status.value}", status=status, kind=ProviderKind.local
            ).as_dict()
            assert d["status"] == status.value

    def test_distinguish_unavailable_vs_unauthorized(self):
        self.registry.record_diagnostic(ProviderDiagnostic(
            provider_id="openai", status=ProviderStatus.unauthorized, kind=ProviderKind.cloud))
        self.registry.record_diagnostic(ProviderDiagnostic(
            provider_id="groq", status=ProviderStatus.unavailable, kind=ProviderKind.cloud))
        diags = {d["provider_id"]: d["status"] for d in self.registry.diagnostics()}
        assert diags["openai"] == "unauthorized" and diags["groq"] == "unavailable"

    def test_provider_info_safe(self):
        for entry in self.registry.provider_info():
            assert "credential_source" not in entry
