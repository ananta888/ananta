"""
ModelProfileResolver — AMR-008

Deterministic resolver: given routing context + loaded profiles, returns
the best-matching ModelProfile with full decision trace.

Precedence ranks (0 = highest):
  0   security_policy           — hard block (secrets + cloud blocked)
  1   request_runtime_override  — explicit per-request model from caller
  2   task_override_map         — per-task explicit override from config
  3   blueprint_rule            — blueprint-specific routing rule
  4   template_rule             — template-specific routing rule
  5   team_rule                 — team-scoped routing rule
  6   risk_class_rule           — risk_class routing rule
  7   model_role_rule           — generic role→profile rule
  8   user_runtime_override     — runtime user-supplied profile_id
  9   global_master_default     — ANANTA_MASTER_LLM_* / DEFAULT_* env default
  10  env_override              — env var MODEL_PROFILE or MODEL_OVERRIDE_ID
  11  capability_match          — best capability-matched profile
  12  legacy_fallback_chain     — fallback chain from routing rules
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
    request_profile_id: str | None = None
    user_profile_id: str | None = None
    env_profile_id: str | None = None
    requires_tools: bool = False
    requires_json: bool = False
    requires_streaming: bool = False
    step_kind: str | None = None
    fallback_group_id: str | None = None
    allow_cloud: bool | None = None
    max_estimated_cost_per_step: float | None = None
    previous_error_type: str | None = None
    repeated_failure_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


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


@dataclass
class FallbackGroupRule:
    group_id: str
    ordered_profiles: list[str] = field(default_factory=list)
    max_total_retries: int = 0
    stop_on_policy_block: bool = True
    stop_on_success: bool = True
    cost_policy: dict[str, Any] = field(default_factory=dict)


@dataclass
class EscalationRule:
    trigger: str
    from_profile: str | None = None
    to_profile: str | None = None
    condition: dict[str, Any] = field(default_factory=dict)


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

    def is_allowed_for_context(self, profile: ModelProfile, ctx: RoutingContext) -> tuple[bool, str]:
        if profile.is_cloud() and ctx.allow_cloud is False:
            return False, "security_policy:cloud_disabled_by_routing_context"
        return self.is_allowed(profile, ctx.context_text)


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
    fallback_groups: dict[str, FallbackGroupRule] = field(default_factory=dict)
    escalation_rules: list[EscalationRule] = field(default_factory=list)

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
        for group_id, raw_group in (data.get("fallback_groups") or {}).items():
            if not isinstance(raw_group, dict):
                continue
            ordered = [
                str(pid).strip()
                for pid in list(raw_group.get("ordered_profiles") or [])
                if str(pid).strip()
            ]
            rules.fallback_groups[str(group_id)] = FallbackGroupRule(
                group_id=str(group_id),
                ordered_profiles=ordered,
                max_total_retries=max(0, int(raw_group.get("max_total_retries") or 0)),
                stop_on_policy_block=bool(raw_group.get("stop_on_policy_block", True)),
                stop_on_success=bool(raw_group.get("stop_on_success", True)),
                cost_policy=dict(raw_group.get("cost_policy") or {}),
            )
        for raw_rule in list(data.get("escalation_rules") or []):
            if not isinstance(raw_rule, dict):
                continue
            rules.escalation_rules.append(EscalationRule(
                trigger=str(raw_rule.get("trigger") or "").strip(),
                from_profile=str(raw_rule.get("from_profile") or "").strip() or None,
                to_profile=str(raw_rule.get("to_profile") or "").strip() or None,
                condition=dict(raw_rule.get("condition") or {}),
            ))
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

    Evaluation order: rank 0 (security hard-block) → rank 1-12 (first match wins).

    Precedence (AMR-022):
      0   security_policy
      1   request_runtime_override
      2   task_override_map
      3   blueprint_rule
      4   template_rule
      5   team_rule
      6   risk_class_rule
      7   model_role_rule
      8   user_runtime_override
      9   global_master_default
      10  env_override
      11  capability_match
      12  legacy_fallback_chain
    """

    def __init__(
        self,
        profiles: list[ModelProfile],
        security_policy: SecurityPolicyChecker | None = None,
        routing_rules: RoutingRules | None = None,
        health_cache: ProviderHealthCache | None = None,
        benchmark_profile_order: list[str] | None = None,
        benchmark_metadata: dict[str, Any] | None = None,
        master_default_profile: ModelProfile | None = None,
    ):
        self._by_id: dict[str, ModelProfile] = {p.profile_id: p for p in profiles if p.enabled}
        self._all_enabled: list[ModelProfile] = [p for p in profiles if p.enabled]
        self.security = security_policy or SecurityPolicyChecker()
        self.rules = routing_rules or RoutingRules()
        self.health = health_cache or ProviderHealthCache()
        self._benchmark_profile_order = [
            str(profile_id or "").strip()
            for profile_id in list(benchmark_profile_order or [])
            if str(profile_id or "").strip()
        ]
        self._benchmark_metadata = dict(benchmark_metadata or {})
        self._master_default = master_default_profile

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
            allowed, reason = self.security.is_allowed_for_context(prof, ctx)
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

        def _try_profile(rank: int, source: str, prof: ModelProfile | None) -> ModelProfile | None:
            if prof is None:
                decisions.append(ResolutionDecision(rank, source, None, False, "no_candidate"))
                return None
            pid = prof.profile_id
            allowed, reason = self.security.is_allowed_for_context(prof, ctx)
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
                        rank, source, pid, False,
                        f"provider_health:unavailable:{prof.provider_id}",
                    )
                )
                return None
            decisions.append(ResolutionDecision(rank, source, pid, True, "accepted"))
            return prof

        # rank 1: request_runtime_override — explicit per-request override
        prof = _try(1, "request_runtime_override", ctx.request_profile_id)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 1, "request_runtime_override")

        # rank 2: task_override_map (task_kind)
        pid = self.rules.task_overrides.get(ctx.task_kind or "")
        prof = _try(2, "task_override_map", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 2, "task_override_map")

        # rank 3: blueprint
        pid = self.rules.blueprint_rules.get(ctx.blueprint_id or "")
        prof = _try(3, "blueprint_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 3, "blueprint_rule")

        # rank 4: template
        pid = self.rules.template_rules.get(ctx.template_id or "")
        prof = _try(4, "template_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 4, "template_rule")

        # rank 5: team
        pid = self.rules.team_rules.get(ctx.team_id or "")
        prof = _try(5, "team_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 5, "team_rule")

        # rank 6: risk_class
        pid = self.rules.risk_class_rules.get(ctx.risk_class or "")
        prof = _try(6, "risk_class_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 6, "risk_class_rule")

        # rank 7: model_role
        pid = self.rules.role_rules.get(ctx.model_role)
        prof = _try(7, "model_role_rule", pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 7, "model_role_rule")

        # rank 8: user_runtime_override — user's configured preference
        prof = _try(8, "user_runtime_override", ctx.user_profile_id)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 8, "user_runtime_override")

        # rank 9: global_master_default — routing-rules global_profile_id, then env-based master
        pid = self.rules.global_profile_id
        if pid:
            prof = _try(9, "global_routing_config", pid)
            if prof:
                return ResolutionResult(prof, decisions, blocked, 9, "global_routing_config")
        prof = _try_profile(9, "global_master_default", self._master_default)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 9, "global_master_default")

        # rank 10: env_override (MODEL_PROFILE / MODEL_OVERRIDE_ID)
        env_pid = ctx.env_profile_id or os.environ.get("MODEL_PROFILE") or os.environ.get("MODEL_OVERRIDE_ID")
        prof = _try(10, "env_override", env_pid)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 10, "env_override")

        # rank 11: capability_match — best enabled profile for requested role
        prof = self._capability_match(ctx, decisions, blocked)
        if prof:
            return ResolutionResult(prof, decisions, blocked, 11, "capability_match")

        # rank 12: legacy_fallback_chain
        for pid in self.rules.fallback_chain:
            prof = _try(12, "legacy_fallback_chain", pid)
            if prof:
                return ResolutionResult(prof, decisions, blocked, 12, "legacy_fallback_chain")

        # exhausted
        decisions.append(
            ResolutionDecision(12, "no_fallback", None, False, "no_usable_profile_found")
        )
        return ResolutionResult(None, decisions, blocked, None, None)

    def _capability_check(self, prof: ModelProfile, ctx: RoutingContext) -> tuple[bool, str]:
        if ctx.requires_tools and not (prof.supports_tools or prof.supports_prompt_json_tools()):
            return False, "capability:tools_required_not_supported"
        if ctx.requires_json and not prof.supports_json:
            return False, "capability:json_required_not_supported"
        if ctx.requires_streaming and not prof.supports_streaming:
            return False, "capability:streaming_required_not_supported"
        context_limit = prof.max_context_for_profile or prof.context_tokens
        approx_context_tokens = int((len(ctx.context_text or "") + 3) / 4)
        if approx_context_tokens > context_limit:
            return False, "capability:context_too_large"
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
        benchmark_order = {
            profile_id: index
            for index, profile_id in enumerate(self._benchmark_profile_order)
        }
        benchmark_ranked = [p for p in candidates if p.profile_id in benchmark_order]
        if benchmark_ranked:
            candidates = sorted(
                candidates,
                key=lambda p: (
                    0 if p.profile_id in benchmark_order else 1,
                    benchmark_order.get(p.profile_id, len(benchmark_order)),
                ),
            )
            decisions.append(
                ResolutionDecision(
                    11,
                    "benchmark_profile_ranking",
                    candidates[0].profile_id if candidates else None,
                    True,
                    "ranking_applied_within_policy_allowed_candidates",
                )
            )

        for prof in candidates:
            allowed, reason = self.security.is_allowed_for_context(prof, ctx)
            if not allowed:
                blocked.append((prof.profile_id, reason))
                decisions.append(
                    ResolutionDecision(11, "capability_match", prof.profile_id, False, reason)
                )
                continue
            cap_ok, cap_reason = self._capability_check(prof, ctx)
            if not cap_ok:
                decisions.append(
                    ResolutionDecision(11, "capability_match", prof.profile_id, False, cap_reason)
                )
                continue
            if not self.health.is_available(prof.provider_id):
                decisions.append(
                    ResolutionDecision(11, "capability_match", prof.profile_id, False,
                                       f"provider_health:unavailable:{prof.provider_id}")
                )
                continue
            decisions.append(
                ResolutionDecision(11, "capability_match", prof.profile_id, True, "best_capability_match")
            )
            return prof
        return None

    def resolve_candidate_chain(self, ctx: RoutingContext) -> tuple[ResolutionResult, list[ModelProfile]]:
        """Return the resolved first profile plus policy-filtered fallback candidates."""
        result = self.resolve(ctx)
        ordered_ids = self._candidate_ids_for_context(ctx, result.profile.profile_id if result.profile else None)
        chain: list[ModelProfile] = []
        seen: set[str] = set()
        blocked = result.blocked_candidates
        decisions = result.decisions

        for pid in ordered_ids:
            if pid in seen:
                continue
            seen.add(pid)
            prof = self._by_id.get(pid)
            if prof is None:
                decisions.append(ResolutionDecision(13, "fallback_candidate_chain", pid, False, f"profile_not_found:{pid}"))
                continue
            allowed, reason = self.security.is_allowed_for_context(prof, ctx)
            if not allowed:
                blocked.append((pid, reason))
                decisions.append(ResolutionDecision(13, "fallback_candidate_chain", pid, False, reason))
                continue
            cap_ok, cap_reason = self._capability_check(prof, ctx)
            if not cap_ok:
                decisions.append(ResolutionDecision(13, "fallback_candidate_chain", pid, False, cap_reason))
                continue
            if not self.health.is_available(prof.provider_id):
                decisions.append(
                    ResolutionDecision(13, "fallback_candidate_chain", pid, False, f"provider_health:unavailable:{prof.provider_id}")
                )
                continue
            decisions.append(ResolutionDecision(13, "fallback_candidate_chain", pid, True, "candidate_available"))
            chain.append(prof)

        if result.profile and (not chain or chain[0].profile_id != result.profile.profile_id):
            chain = [result.profile] + [p for p in chain if p.profile_id != result.profile.profile_id]
        return result, chain

    def _candidate_ids_for_context(self, ctx: RoutingContext, resolved_profile_id: str | None) -> list[str]:
        group_id = ctx.fallback_group_id
        if not group_id and resolved_profile_id:
            prof = self._by_id.get(resolved_profile_id)
            group_id = prof.fallback_group if prof else None
        if group_id and group_id in self.rules.fallback_groups:
            return list(self.rules.fallback_groups[group_id].ordered_profiles)
        grouped = [
            p for p in self._all_enabled
            if group_id and p.fallback_group == group_id
        ]
        if grouped:
            return [p.profile_id for p in sorted(grouped, key=lambda p: (p.fallback_rank is None, p.fallback_rank or 0))]
        if self.rules.fallback_chain:
            ids = list(self.rules.fallback_chain)
            if resolved_profile_id and resolved_profile_id not in ids:
                ids.insert(0, resolved_profile_id)
            return ids
        if resolved_profile_id:
            return [resolved_profile_id]
        return [p.profile_id for p in self._all_enabled]

    def benchmark_ranking_read_model(self) -> dict[str, Any]:
        return {
            "active": bool(self._benchmark_profile_order),
            "profile_order": list(self._benchmark_profile_order),
            "sample_metadata": dict(self._benchmark_metadata),
            "policy_boundary": "ranking_only_after_policy_and_capability_filters",
        }

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
