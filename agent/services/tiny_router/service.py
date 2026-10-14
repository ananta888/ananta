"""Bounded Tiny -> Small -> Main candidate routing service."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from agent.services.tiny_router.adapters import NeedleCandidateAdapter, OpenAICompatibleActionAdapter
from agent.services.tiny_router.base import (
    NullTinyRouterTelemetrySink,
    TinyActionModelAdapter,
    TinyRouterTelemetrySink,
)
from agent.services.tiny_router.decision_provider_adapter import DecisionProviderToolAdapter
from agent.services.tiny_router.multi_tool_adapter import MultiToolPlanAdapter
from agent.services.tiny_router.observability import TinyRouterObserver
from agent.services.tiny_router.parallel_decision import ParallelDecisionAdapter
from agent.services.tiny_router.preselection import AllowedToolPreselector
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tiny_router.types import (
    STATUS_CANDIDATE,
    STATUS_DISABLED,
    STATUS_ESCALATE,
    STATUS_PLAN,
    STATUS_SHADOW_CANDIDATE,
    STATUS_SHADOW_PLAN,
    AdapterRequest,
    MultiToolRoutingDecision,
    RoutingAttempt,
    RoutingDecision,
)
from agent.services.tiny_router.validation import CandidateValidator
from ananta_contracts.multi_tool_plan import PLAN, MultiToolPlan

MODE_DISABLED = "disabled"
MODE_SHADOW = "shadow"
MODE_ACTIVE = "active"
_MODES = {MODE_DISABLED, MODE_SHADOW, MODE_ACTIVE}


class TinyToolRouterService:
    """Selects a candidate only. It has no executor or policy mutation port."""

    def __init__(
        self,
        *,
        catalog: ProfileCatalog | None = None,
        adapters: Sequence[TinyActionModelAdapter] | None = None,
        schema_adapter: Any | None = None,
        registry: Any | None = None,
        telemetry_sink: TinyRouterTelemetrySink | None = None,
        clock: Callable[[], float] = time.monotonic,
        plan_adapters: Sequence[TinyActionModelAdapter] | None = None,
    ) -> None:
        if schema_adapter is None:
            from agent.services.tool_schema_adapter_service import get_tool_schema_adapter

            schema_adapter = get_tool_schema_adapter()
        if registry is None:
            from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

            registry = get_ananta_tool_registry_service()
        self._catalog = catalog or ProfileCatalog.load()
        adapter_rows = adapters or (
            NeedleCandidateAdapter(),
            OpenAICompatibleActionAdapter(),
            ParallelDecisionAdapter(),
            DecisionProviderToolAdapter(),
        )
        self._adapters = {item.adapter_id: item for item in adapter_rows}
        plan_rows = plan_adapters if plan_adapters is not None else (MultiToolPlanAdapter(),)
        self._plan_adapters = {item.adapter_id: item for item in plan_rows}
        self._schema_adapter = schema_adapter
        self._registry = registry
        self._validator = CandidateValidator()
        self._preselector = AllowedToolPreselector()
        self._observer = TinyRouterObserver(telemetry_sink or NullTinyRouterTelemetrySink())
        self._clock = clock

    def route(
        self,
        *,
        prompt: str,
        allowed_tools: Sequence[str] | None,
        config: Mapping[str, Any] | None = None,
        mutation_mode: str = "read_only",
        cancel_check: Callable[[], bool] | None = None,
    ) -> RoutingDecision:
        started = self._clock()
        cfg = self._normalize_config(config)
        mode = cfg["mode"]
        shadow = mode == MODE_SHADOW
        if mode == MODE_DISABLED or cfg["kill_switch"]:
            return self._finish(
                RoutingDecision(
                    STATUS_DISABLED,
                    "kill_switch_active" if cfg["kill_switch"] else "router_disabled",
                    shadow=shadow,
                ),
                started,
                len(str(prompt or "")),
            )
        if cancel_check is not None and cancel_check():
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "invocation_cancelled", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if self._catalog.safe_mode:
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "profile_catalog_safe_mode", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if not str(prompt or "").strip():
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "empty_prompt", shadow=shadow),
                started,
                0,
            )
        if len(str(prompt)) > cfg["max_prompt_chars"]:
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "prompt_too_large", shadow=shadow),
                started,
                len(str(prompt)),
            )
        allowed = tuple(str(item or "").strip() for item in (allowed_tools or ()) if str(item or "").strip())
        if not allowed:
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "allowed_tool_scope_empty", shadow=shadow),
                started,
                len(str(prompt)),
            )
        tools = self._authorized_risk_subset(
            self._schema_adapter.get_openai_tools(list(allowed)),
            cfg["allowed_risk_classes"],
            mutation_mode=mutation_mode,
        )
        if not tools:
            return self._finish(
                RoutingDecision(STATUS_ESCALATE, "no_eligible_allowed_tools", shadow=shadow),
                started,
                len(str(prompt)),
            )
        profiles, rejected = self._catalog.ordered(
            cfg["profile_order"],
            commercial_use=cfg["commercial_use"],
            allow_research_only=cfg["allow_research_only"],
        )
        attempts: list[RoutingAttempt] = [
            RoutingAttempt(profile_id, "unknown", "rejected", reason, 0.0, 0) for profile_id, reason in rejected
        ]
        if not profiles:
            return self._finish(
                RoutingDecision(
                    STATUS_ESCALATE,
                    "no_eligible_profiles",
                    attempts=tuple(attempts),
                    shadow=shadow,
                ),
                started,
                len(str(prompt)),
            )
        deadline = started + cfg["max_total_ms"] / 1000.0
        for profile in profiles[: cfg["max_hops"]]:
            if cancel_check is not None and cancel_check():
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "cancelled",
                        "invocation_cancelled",
                        0.0,
                        0,
                    )
                )
                break
            if self._clock() >= deadline:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "skipped",
                        "routing_deadline_exceeded",
                        0.0,
                        0,
                    )
                )
                break
            adapter = self._adapters.get(profile.adapter)
            if adapter is None:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "unavailable",
                        "adapter_not_registered",
                        0.0,
                        0,
                    )
                )
                continue
            available, availability_reason = adapter.is_available(profile)
            if not available:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "unavailable",
                        availability_reason,
                        0.0,
                        0,
                    )
                )
                continue
            selected = self._preselector.select(
                str(prompt),
                tools,
                top_k=min(cfg["top_k"], profile.max_tools),
            )
            remaining_ms = max(1, int((deadline - self._clock()) * 1000.0))
            try:
                result = adapter.propose(
                    AdapterRequest(
                        str(prompt),
                        tuple(selected),
                        profile,
                        remaining_ms,
                    )
                )
            except Exception as exc:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "failed",
                        self._adapter_error_code(exc),
                        0.0,
                        len(selected),
                    )
                )
                continue
            rejection = self._inference_rejection(deadline, cancel_check)
            if rejection:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "cancelled" if rejection == "invocation_cancelled" else "failed",
                        rejection,
                        result.latency_ms,
                        len(selected),
                    )
                )
                break
            validated = self._validator.validate(
                result.payload,
                tools=selected,
                profile=profile,
                adapter_id=adapter.adapter_id,
                min_confidence=cfg["min_confidence"],
            )
            attempts.append(
                RoutingAttempt(
                    profile.profile_id,
                    profile.tier,
                    validated.status,
                    validated.reason_code,
                    result.latency_ms,
                    len(selected),
                )
            )
            if validated.candidate:
                return self._finish(
                    RoutingDecision(
                        STATUS_SHADOW_CANDIDATE if shadow else STATUS_CANDIDATE,
                        "shadow_candidate_validated" if shadow else "candidate_validated",
                        candidate=validated.candidate,
                        attempts=tuple(attempts),
                        escalation_tier="main" if shadow else profile.tier,
                        shadow=shadow,
                    ),
                    started,
                    len(str(prompt)),
                )
        return self._finish(
            RoutingDecision(
                STATUS_ESCALATE,
                attempts[-1].reason_code if attempts else "all_profiles_failed",
                attempts=tuple(attempts),
                escalation_tier="main",
                shadow=shadow,
            ),
            started,
            len(str(prompt)),
        )

    def plan(
        self,
        *,
        prompt: str,
        allowed_tools: Sequence[str] | None,
        config: Mapping[str, Any] | None = None,
        mutation_mode: str = "read_only",
        cancel_check: Callable[[], bool] | None = None,
    ) -> MultiToolRoutingDecision:
        """A multi-tool plan (PDT): several independent calls or an escalation.

        Additive to ``route``; the single-candidate path is unchanged. Only active
        with ``multi_tool_plan: true`` in the config (default off). A negative
        ``enabled`` field never produces a call, and execution gates stay outside
        the router.
        """
        started = self._clock()
        cfg = self._normalize_config(config)
        shadow = cfg["mode"] == MODE_SHADOW
        if not cfg["multi_tool_plan"]:
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_DISABLED, "multi_tool_plan_disabled", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if (config or {}).get("tool_dependencies"):
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "dependent_tools_require_system2", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if cfg["mode"] == MODE_DISABLED or cfg["kill_switch"]:
            return self._finish_plan(
                MultiToolRoutingDecision(
                    STATUS_DISABLED,
                    "kill_switch_active" if cfg["kill_switch"] else "router_disabled",
                    shadow=shadow,
                ),
                started,
                len(str(prompt or "")),
            )
        if cancel_check is not None and cancel_check():
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "invocation_cancelled", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if self._catalog.safe_mode:
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "profile_catalog_safe_mode", shadow=shadow),
                started,
                len(str(prompt or "")),
            )
        if not str(prompt or "").strip():
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "empty_prompt", shadow=shadow),
                started,
                0,
            )
        if len(str(prompt)) > cfg["max_prompt_chars"]:
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "prompt_too_large", shadow=shadow),
                started,
                len(str(prompt)),
            )
        allowed = tuple(str(item or "").strip() for item in (allowed_tools or ()) if str(item or "").strip())
        if not allowed:
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "allowed_tool_scope_empty", shadow=shadow),
                started,
                len(str(prompt)),
            )
        tools = self._authorized_risk_subset(
            self._schema_adapter.get_openai_tools(list(allowed)),
            cfg["allowed_risk_classes"],
            mutation_mode=mutation_mode,
        )
        if not tools:
            return self._finish_plan(
                MultiToolRoutingDecision(STATUS_ESCALATE, "no_eligible_allowed_tools", shadow=shadow),
                started,
                len(str(prompt)),
            )
        profiles, rejected = self._catalog.ordered(
            cfg["profile_order"],
            commercial_use=cfg["commercial_use"],
            allow_research_only=cfg["allow_research_only"],
        )
        attempts: list[RoutingAttempt] = [
            RoutingAttempt(profile_id, "unknown", "rejected", reason, 0.0, 0) for profile_id, reason in rejected
        ]
        if not profiles:
            return self._finish_plan(
                MultiToolRoutingDecision(
                    STATUS_ESCALATE, "no_eligible_profiles", attempts=tuple(attempts), shadow=shadow
                ),
                started,
                len(str(prompt)),
            )
        deadline = started + cfg["max_total_ms"] / 1000.0
        for profile in profiles[: cfg["max_hops"]]:
            if cancel_check is not None and cancel_check():
                attempts.append(
                    RoutingAttempt(profile.profile_id, profile.tier, "cancelled", "invocation_cancelled", 0.0, 0)
                )
                break
            if self._clock() >= deadline:
                attempts.append(
                    RoutingAttempt(profile.profile_id, profile.tier, "skipped", "routing_deadline_exceeded", 0.0, 0)
                )
                break
            adapter = self._plan_adapters.get(profile.adapter)
            if adapter is None:
                attempts.append(
                    RoutingAttempt(profile.profile_id, profile.tier, "unavailable", "adapter_not_registered", 0.0, 0)
                )
                continue
            available, availability_reason = adapter.is_available(profile)
            if not available:
                attempts.append(
                    RoutingAttempt(profile.profile_id, profile.tier, "unavailable", availability_reason, 0.0, 0)
                )
                continue
            selected = self._preselector.select(str(prompt), tools, top_k=min(cfg["top_k"], profile.max_tools))
            remaining_ms = max(1, int((deadline - self._clock()) * 1000.0))
            try:
                result = adapter.propose(AdapterRequest(str(prompt), tuple(selected), profile, remaining_ms))
            except Exception as exc:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id, profile.tier, "failed", self._adapter_error_code(exc), 0.0, len(selected)
                    )
                )
                continue
            plan = result.payload
            rejection = self._inference_rejection(deadline, cancel_check)
            if rejection:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "cancelled" if rejection == "invocation_cancelled" else "failed",
                        rejection,
                        result.latency_ms,
                        len(selected),
                    )
                )
                break
            if not isinstance(plan, MultiToolPlan):
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "invalid",
                        result.reason_code or "plan_invalid",
                        result.latency_ms,
                        len(selected),
                    )
                )
                continue
            calls = plan.calls()
            threshold = profile.min_confidence if cfg["min_confidence"] is None else cfg["min_confidence"]
            if plan.status != PLAN or len(calls) > cfg["max_plan_calls"]:
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "invalid" if calls else "abstain",
                        plan.reason,
                        result.latency_ms,
                        len(selected),
                    )
                )
                continue
            selected_schemas = {item["function"]["name"]: item for item in selected}
            if any(
                not self._validator.validate(
                    {
                        "tool_calls": [
                            {"name": step.tool, "arguments": dict(step.arguments), "confidence": step.confidence}
                        ]
                    },
                    tools=selected,
                    profile=profile,
                    adapter_id=adapter.adapter_id,
                    min_confidence=threshold,
                ).candidate
                for step in calls
            ) or any(step.tool not in selected_schemas for step in calls):
                attempts.append(
                    RoutingAttempt(
                        profile.profile_id,
                        profile.tier,
                        "invalid",
                        "plan_call_validation_failed",
                        result.latency_ms,
                        len(selected),
                    )
                )
                continue
            attempts.append(
                RoutingAttempt(
                    profile.profile_id,
                    profile.tier,
                    "planned" if calls else "abstain",
                    plan.reason,
                    result.latency_ms,
                    len(selected),
                )
            )
            if calls:
                return self._finish_plan(
                    MultiToolRoutingDecision(
                        STATUS_SHADOW_PLAN if shadow else STATUS_PLAN,
                        "shadow_plan_ready" if shadow else "plan_ready",
                        plan=plan,
                        attempts=tuple(attempts),
                        escalation_tier="main" if shadow else profile.tier,
                        shadow=shadow,
                    ),
                    started,
                    len(str(prompt)),
                )
        return self._finish_plan(
            MultiToolRoutingDecision(
                STATUS_ESCALATE,
                attempts[-1].reason_code if attempts else "all_profiles_failed",
                attempts=tuple(attempts),
                escalation_tier="main",
                shadow=shadow,
            ),
            started,
            len(str(prompt)),
        )

    def _inference_rejection(self, deadline: float, cancel_check: Callable[[], bool] | None) -> str:
        if cancel_check is not None and cancel_check():
            return "invocation_cancelled"
        if self._clock() >= deadline:
            return "routing_deadline_exceeded"
        return ""

    def _finish_plan(
        self,
        decision: MultiToolRoutingDecision,
        started: float,
        prompt_chars: int,
    ) -> MultiToolRoutingDecision:
        final = MultiToolRoutingDecision(
            decision.status,
            decision.reason_code,
            plan=decision.plan,
            attempts=decision.attempts,
            escalation_tier=decision.escalation_tier,
            elapsed_ms=max(0.0, (self._clock() - started) * 1000.0),
            shadow=decision.shadow,
        )
        self._observer.record_plan(final, prompt_chars=prompt_chars)
        return final

    def _authorized_risk_subset(
        self,
        tools: Sequence[Mapping[str, Any]],
        allowed_risk_classes: frozenset[str],
        *,
        mutation_mode: str,
    ) -> tuple[Mapping[str, Any], ...]:
        result: list[Mapping[str, Any]] = []
        for item in tools:
            function = item.get("function") if isinstance(item, Mapping) else None
            name = str((function or {}).get("name") or "")
            spec = self._registry.get_tool(name)
            risk = str(getattr(spec, "risk_class", "") or "")
            if spec is None or risk not in allowed_risk_classes:
                continue
            if mutation_mode == "read_only" and risk != "read":
                continue
            result.append(item)
        return tuple(result)

    @staticmethod
    def _normalize_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
        raw = dict(config or {})
        mode = str(raw.get("mode") or MODE_DISABLED).strip().lower()
        if mode not in _MODES:
            mode = MODE_DISABLED
        min_confidence = raw.get("min_confidence")
        if min_confidence is not None:
            try:
                min_confidence = max(0.0, min(float(min_confidence), 1.0))
            except (TypeError, ValueError):
                min_confidence = None
        return {
            "mode": mode,
            "kill_switch": bool(raw.get("kill_switch", False)),
            "profile_order": tuple(str(item) for item in (raw.get("profile_order") or [])),
            "top_k": max(1, min(int(raw.get("top_k") or 5), 100)),
            "max_hops": max(1, min(int(raw.get("max_hops") or 2), 3)),
            "max_total_ms": max(10, min(int(raw.get("max_total_ms") or 1500), 30000)),
            "max_prompt_chars": max(128, min(int(raw.get("max_prompt_chars") or 4000), 32000)),
            "min_confidence": min_confidence,
            "allowed_risk_classes": frozenset(
                str(item or "").strip() for item in (raw.get("allowed_risk_classes") or ["read"])
            ),
            "commercial_use": bool(raw.get("commercial_use", True)),
            "allow_research_only": bool(raw.get("allow_research_only", False)),
            "multi_tool_plan": bool(raw.get("multi_tool_plan", False)),
            "max_plan_calls": max(1, min(int(raw.get("max_plan_calls") or 4), 32)),
        }

    @staticmethod
    def _adapter_error_code(exc: Exception) -> str:
        name = exc.__class__.__name__.lower()
        if "timeout" in name:
            return "adapter_timeout"
        if "connection" in name:
            return "adapter_unavailable"
        return "adapter_failed"

    def _finish(
        self,
        decision: RoutingDecision,
        started: float,
        prompt_chars: int,
    ) -> RoutingDecision:
        final = RoutingDecision(
            decision.status,
            decision.reason_code,
            candidate=decision.candidate,
            attempts=decision.attempts,
            escalation_tier=decision.escalation_tier,
            elapsed_ms=max(0.0, (self._clock() - started) * 1000.0),
            shadow=decision.shadow,
        )
        self._observer.record(final, prompt_chars=prompt_chars)
        return final


_tiny_tool_router_service: TinyToolRouterService | None = None


def get_tiny_tool_router_service() -> TinyToolRouterService:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            existing = current_app.extensions.get("tiny_tool_router_service")
            if isinstance(existing, TinyToolRouterService):
                return existing
            service = TinyToolRouterService(telemetry_sink=current_app.extensions.get("tiny_router_telemetry_sink"))
            current_app.extensions["tiny_tool_router_service"] = service
            return service
    except (ImportError, RuntimeError):
        pass
    global _tiny_tool_router_service
    if _tiny_tool_router_service is None:
        _tiny_tool_router_service = TinyToolRouterService()
    return _tiny_tool_router_service
