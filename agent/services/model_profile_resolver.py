"""
ModelProfileResolver — AMR-008

Deterministic resolver: given routing context + loaded profiles, returns
the best-matching ModelProfile with full decision trace.

Precedence ranks (0 = highest):
  0  security_policy         — hard block (secrets + cloud blocked)
  1  task_override_map       — per-task explicit override from config
  2  blueprint_rule          — blueprint-specific routing rule
  3  template_rule           — template-specific routing rule
  4  team_rule               — team-scoped routing rule
  5  risk_class_rule         — risk_class routing rule
  6  model_role_rule         — generic role→profile rule
  7  global_routing_config   — global provider/model setting
  8  env_override            — env var MODEL_PROFILE or MODEL_OVERRIDE_ID
  9  user_runtime_override   — runtime user-supplied profile_id
 10  capability_match        — best capability-matched profile
 11  legacy_default          — DEFAULT_PROVIDER / DEFAULT_MODEL fallback
"""
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any

from agent.services.model_profile_loader import ModelProfile

logger = logging.getLogger(__name__)

# Patterns that indicate a prompt/context contains secrets
_DEFAULT_SECRET_PATTERNS: list[str] = [
    r"(?i)api[_\-]?key\s*[:=]\s*\S{10,}",
    r"(?i)secret[_\-]?key\s*[:=]\s*\S{10,}",
    r"(?i)password\s*[:=]\s*\S{6,}",
    r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}",
    r"(?i)AWS_SECRET_ACCESS_KEY\s*[:=]\s*\S+",
    r"sk-[A-Za-z0-9]{20,}",
]


@dataclass
class RoutingContext:
    """All facts known at resolution time."""
    model_role: str = "any"
    blueprint_id: str | None = None
    template_id: str | None = None
    team_id: str | None = None
    task_kind: str | None = None
    risk_class: str | None = None
    context_text: str = ""
    user_profile_id: str | None = None
    env_profile_id: str | None = None
    requires_tools: bool = False
    requires_json: bool = False
    requires_streaming: bool = False


@dataclass
class ResolutionDecision:
    """A single step in the resolution trace."""
    rank: int
    source: str
    profile_id: str | None
    accepted: bool
    reason: str


@dataclass
class ResolutionResult:
    profile: ModelProfile | None
    decisions: list[ResolutionDecision]
    blocked_candidates: list[tuple[str, str]]
    final_rank: int | None
    final_source: str | None

    @property
    def ok(self) -> bool:
        return self.profile is not None

    def summary(self) -> str:
        if self.profile:
            return (
                f"resolved:{self.profile.profile_id} "
                f"via:{self.final_source}(rank={self.final_rank})"
            )
        return "no_profile_resolved"


class SecurityPolicyChecker:
    """Rank-0 check: blocks cloud profiles when secrets are present."""

    def __init__(
        self,
        block_cloud_with_secrets: bool = True,
        allowed_cloud_providers: list[str] | None = None,
        extra_patterns: list[str] | None = None,
    ):
        self.block_cloud_with_secrets = block_cloud_with_secrets
        self.allowed_cloud_providers: set[str] = set(allowed_cloud_providers or [])
        patterns = _DEFAULT_SECRET_PATTERNS + (extra_patterns or [])
        self._compiled = [re.compile(p) for p in patterns]

    def context_has_secrets(self, text: str) -> bool:
        if not text:
            return False
        return any(p.search(text) for p in self._compiled)

    def is_allowed(self, profile: ModelProfile, context_text: str) -> tuple[bool, str]:
        if not profile.is_cloud():
            return True, "local_profile_always_allowed"
        if self.block_cloud_with_secrets and self.context_has_secrets(context_text):
            return False, "security_policy:secrets_detected_cloud_blocked"
        if self.allowed_cloud_providers and profile.provider_id not in self.allowed_cloud_providers:
            return False, f"security_policy:provider_not_in_allowlist:{profile.provider_id}"
        if not profile.cloud_allowed:
            return False, "security_policy:cloud_allowed=false_on_profile"
        if not profile.block_secret_context and profile.is_cloud():
            return (
                False,
                "security_policy:cloud_profile_missing_block_secret_context",
            )
        return True, "security_policy:pass"


@dataclass
class RoutingRules:
    """Loaded routing rules from config/routing file."""
    task_overrides: dict[str, str] = field(default_factory=dict)
    blueprint_rules: dict[str, str] = field(default_factory=dict)
    template_rules: dict[str, str] = field(default_factory=dict)
    team_rules: dict[str, str] = field(default_factory=dict)
    risk_class_rules: dict[str, str] = field(default_factory=dict)
    role_rules: dict[str, str] = field(default_factory=dict)
    global_profile_id: str | None = None
    fallback_chain: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutingRules":
        rules = cls()
        for item in data.get("routing_rules") or []:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            pid = item.get("profile_id")
            if not pid:
                continue
            if item.get("task_kind"):
                rules.task_overrides[item["task_kind"]] = pid
            elif item.get("blueprint_id"):
                rules.blueprint_rules[item["blueprint_id"]] = pid
            elif item.get("template_id"):
                rules.template_rules[item["template_id"]] = pid
            elif item.get("team_id"):
                rules.team_rules[item["team_id"]] = pid
            elif item.get("risk_class"):
                rules.risk_class_rules[item["risk_class"]] = pid
            elif item.get("model_role"):
                rules.role_rules[item["model_role"]] = pid
        rules.fallback_chain = list(data.get("fallback_chain") or [])
        return rules


class ProviderHealthCache:
    """Simple in-memory cache for provider availability (AMR-017)."""

    def __init__(self) -> None:
        self._unavailable: dict[str, float] = {}
        self._ttl_seconds: float = 60.0

    def mark_unavailable(self, provider_id: str) -> None:
        import time
        self._unavailable[provider_id] = time.time()

    def is_available(self, provider_id: str) -> bool:
        import time
        last_fail = self._unavailable.get(provider_id)
        if last_fail is None:
            return True
        return (time.time() - last_fail) > self._ttl_seconds

    def reset(self, provider_id: str) -> None:
        self._unavailable.pop(provider_id, None)


class ModelProfileResolver:
    """
    Deterministic, traceable profile resolver.

    Evaluation order: rank 0 (security hard-block) → rank 1-11 (first match wins).
    """

    def __init__(
        self,
        profiles: list[ModelProfile],
        security_policy: SecurityPolicyChecker | None = None,
        routing_rules: RoutingRules | None = None,
        health_cache: ProviderHealthCache | None = None,
    ):
        self._by_id: dict[str, ModelProfile] = {p.profile_id: p for p in profiles if p.enabled}
        self._all_enabled: list[ModelProfile] = [p for p in profiles if p.enabled]
        self.security = security_policy or SecurityPolicyChecker()
        self.rules = routing_rules or RoutingRules()
        self.health = health_cache or ProviderHealthCache()

    def resolve(self, ctx: RoutingContext) -> ResolutionResult:
        decisions: list[ResolutionDecision] = []
        blocked: list[tuple[str, str]] = []

        def _try(rank: int, source: str, pid: str | None) -> ModelProfile | None:
            if not pid:
                decisions.append(ResolutionDecision(rank, source, None, False, "no_candidate"))
                return None
            prof = self._by_id.get(pid)
            if not prof:
                decisions.append(
                    ResolutionDecision(rank, source, pid, False, f"profile_not_found:{pid}")
                )
                return None
            allowed, reason = self.security.is_allowed(prof, ctx.context_text)
            if not allowed:
                blocked.append((pid, reason))
                decisions.append(ResolutionDecision(rank, source, pid, False, reason))
                return None
            cap_ok, cap_reason = self._capability_check(prof, ctx)
            if not cap_ok:
                decisions.append(ResolutionDecision(rank, source, pid, False, cap_reason))
                return None
            if not self.health.is_available(prof.provider_id):
                decisions.append(
                    ResolutionDecision(
                        rank,
                        source,
                        pid,
                        False,
                        f"provider_health:unavailable:{prof.provider_id}",
                    )
                )
                return None
            decisions.append(ResolutionDecision(rank, source, pid, True, "accepted"))
            return prof

        # rank 1: task_kind override
        pid = self.rules.task_overrides.get(ctx.task_kind or "")
        prof = _try(1, "task_override_map", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 1, "task_override_map")

        # rank 2: blueprint
        pid = self.rules.blueprint_rules.get(ctx.blueprint_id or "")
        prof = _try(2, "blueprint_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 2, "blueprint_rule")

        # rank 3: template
        pid = self.rules.template_rules.get(ctx.template_id or "")
        prof = _try(3, "template_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 3, "template_rule")

        # rank 4: team
        pid = self.rules.team_rules.get(ctx.team_id or "")
        prof = _try(4, "team_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 4, "team_rule")

        # rank 5: risk_class
        pid = self.rules.risk_class_rules.get(ctx.risk_class or "")
        prof = _try(5, "risk_class_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 5, "risk_class_rule")

        # rank 6: model_role
        pid = self.rules.role_rules.get(ctx.model_role)
        prof = _try(6, "model_role_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 6, "model_role_rule")

        # rank 7: global routing config
        pid = self.rules.global_profile_id
        prof = _try(7, "global_routing_config", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 7, "global_routing_config")

        # rank 8: env var
        env_pid = ctx.env_profile_id or os.environ.get("MODEL_PROFILE") or os.environ.get("MODEL_OVERRIDE_ID")
        prof = _try(8, "env_override", env_pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 8, "env_override")

        # rank 9: user runtime override
        prof = _try(9, "user_runtime_override", ctx.user_profile_id)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 9, "user_runtime_override")

        # rank 10: capability match — best enabled profile for requested role
        prof = self._capability_match(ctx, decisions, blocked)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 10, "capability_match")

        # rank 11: fallback chain
        for pid in self.rules.fallback_chain:
            prof = _try(11, "legacy_fallback_chain", pid)
            if prof:
                return ResolutionResult(prof, decisions, blocked, 11, "legacy_fallback_chain")

        # exhausted
        decisions.append(
            ResolutionDecision(11, "no_fallback", None, False, "no_usable_profile_found")
        )
        return ResolutionResult(None, decisions, blocked, None, None)

    def _capability_check(self, prof: ModelProfile, ctx: RoutingContext) -> tuple[bool, str]:
        if ctx.requires_tools and not prof.supports_tools:
            return False, "capability:tools_required_not_supported"
        if ctx.requires_json and not prof.supports_json:
            return False, "capability:json_required_not_supported"
        if ctx.requires_streaming and not prof.supports_streaming:
            return False, "capability:streaming_required_not_supported"
        return True, "capability:ok"

    def _capability_match(
        self,
        ctx: RoutingContext,
        decisions: list[ResolutionDecision],
        blocked: list[tuple[str, str]],
    ) -> ModelProfile | None:
        candidates = [p for p in self._all_enabled]

        # prefer matching role
        role_matched = [p for p in candidates if p.model_role == ctx.model_role or p.model_role == "any"]
        if role_matched:
            candidates = role_matched

        for prof in candidates:
            allowed, reason = self.security.is_allowed(prof, ctx.context_text)
            if not allowed:
                blocked.append((prof.profile_id, reason))
                decisions.append(
                    ResolutionDecision(10, "capability_match", prof.profile_id, False, reason)
                )
                continue
            cap_ok, cap_reason = self._capability_check(prof, ctx)
            if not cap_ok:
                decisions.append(
                    ResolutionDecision(10, "capability_match", prof.profile_id, False, cap_reason)
                )
                continue
            if not self.health.is_available(prof.provider_id):
                decisions.append(
                    ResolutionDecision(10, "capability_match", prof.profile_id, False,
                                       f"provider_health:unavailable:{prof.provider_id}")
                )
                continue
            decisions.append(
                ResolutionDecision(10, "capability_match", prof.profile_id, True, "best_capability_match")
            )
            return prof
        return None

    def resolve_with_fallback(
        self,
        ctx: RoutingContext,
        *,
        legacy_provider: str = "lmstudio",
        legacy_model: str = "auto",
    ) -> tuple[ResolutionResult, dict[str, str]]:
        """
        AMR-017: Resolve with graceful degradation to legacy provider/model.

        Returns (result, fallback_info). fallback_info is empty if resolver succeeded.
        fallback_info keys: legacy_provider, legacy_model, reason.
        """
        result = self.resolve(ctx)
        if result.ok:
            return result, {}

        # Exhausted all profiles — degrade to legacy
        fallback_info = {
            "legacy_provider": legacy_provider,
            "legacy_model": legacy_model,
            "reason": "no_profile_resolved:degraded_to_legacy_provider_model",
        }
        logger.warning(
            "model_profile_resolver: no profile resolved for ctx=%s, falling back to %s/%s",
            ctx.model_role,
            legacy_provider,
            legacy_model,
        )
        return result, fallback_info

    def report_provider_failure(self, provider_id: str) -> None:
        """AMR-017: Called after a provider request fails; marks it temporarily unavailable."""
        self.health.mark_unavailable(provider_id)
        logger.warning("model_profile_resolver: provider marked unavailable: %s", provider_id)

    def report_provider_recovery(self, provider_id: str) -> None:
        """AMR-017: Called when a provider recovers."""
        self.health.reset(provider_id)
        logger.info("model_profile_resolver: provider recovered: %s", provider_id)
