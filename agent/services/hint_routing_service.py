from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.model_output_format_profile_service import get_model_output_format_profile_service
from agent.services.routing_decision_service import get_routing_decision_service

_KNOWN_HINTS = {
    "hint:planning",
    "hint:context_compaction",
    "hint:code",
    "hint:security_review",
    "hint:summarize",
    "hint:cheap_classify",
    "hint:local_embedding",
    "hint:repair",
}


@dataclass(frozen=True)
class HintRoutingDecision:
    hint: str
    available: bool
    provider: str | None
    model: str | None
    runtime_profile: str | None
    llm_scope: str
    output_format_profile: dict[str, Any]
    reason: str
    chain: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hint": self.hint,
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "runtime_profile": self.runtime_profile,
            "llm_scope": self.llm_scope,
            "output_format_profile": dict(self.output_format_profile),
            "reason": self.reason,
            "chain": dict(self.chain),
        }


class HintRoutingService:
    """Resolves semantic hint:* routes into policy-gated provider/model decisions."""

    def _local_only(self, hint: str, cfg: dict[str, Any]) -> bool:
        return hint in set(cfg.get("local_only_hints") or [])

    def _cloud_allowed(self, hint: str, cfg: dict[str, Any], cloud_allowed: bool) -> bool:
        if not cloud_allowed:
            return False
        return hint in set(cfg.get("cloud_allowed_hints") or [])

    def _defaults_for_hint(self, hint: str) -> tuple[str | None, str | None, str | None]:
        if hint == "hint:context_compaction":
            return "ollama", None, "context_compaction"
        if hint == "hint:planning":
            return None, None, "planning"
        if hint == "hint:security_review":
            return None, None, "security_review"
        if hint == "hint:code":
            return None, None, "code"
        if hint == "hint:summarize":
            return None, None, "summarize"
        if hint == "hint:cheap_classify":
            return "ollama", None, "cheap_classify"
        if hint == "hint:local_embedding":
            return "ollama", None, "local_embedding"
        if hint == "hint:repair":
            return None, None, "repair"
        return None, None, None

    def resolve(
        self,
        *,
        hint: str,
        cfg: dict[str, Any] | None,
        task_kind: str | None = None,
        cloud_allowed: bool = True,
        provider: str | None = None,
        model: str | None = None,
        runtime_profile_name: str | None = None,
        run_telemetry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg_all = dict(cfg or {})
        hint_cfg = dict(cfg_all.get("hint_routing") or {})
        enabled = bool(hint_cfg.get("enabled", False))
        normalized_hint = str(hint or "").strip().lower()
        unknown_action = str(hint_cfg.get("unknown_hint_action") or "mark_unavailable").strip().lower()

        if not enabled:
            return HintRoutingDecision(
                hint=normalized_hint,
                available=True,
                provider=provider,
                model=model,
                runtime_profile=runtime_profile_name,
                llm_scope="compatibility",
                output_format_profile={},
                reason="hint_routing_disabled",
                chain={},
            ).as_dict()

        if normalized_hint not in _KNOWN_HINTS:
            available = unknown_action != "mark_unavailable"
            return HintRoutingDecision(
                hint=normalized_hint,
                available=available,
                provider=provider,
                model=model,
                runtime_profile=runtime_profile_name,
                llm_scope="local_only" if not cloud_allowed else "external_cloud_allowed",
                output_format_profile={},
                reason=f"unknown_hint:{unknown_action}",
                chain={},
            ).as_dict()

        default_provider, default_model, default_runtime_profile = self._defaults_for_hint(normalized_hint)
        resolved_provider = provider or default_provider
        resolved_model = model or default_model
        resolved_runtime_profile = runtime_profile_name or default_runtime_profile

        local_only = self._local_only(normalized_hint, hint_cfg)
        cloud_ok_for_hint = self._cloud_allowed(normalized_hint, hint_cfg, cloud_allowed)
        llm_scope = "local_only" if (local_only or not cloud_ok_for_hint) else "external_cloud_allowed"

        if llm_scope == "local_only" and resolved_provider and resolved_provider in {"openai", "anthropic", "azure_openai"}:
            return HintRoutingDecision(
                hint=normalized_hint,
                available=False,
                provider=resolved_provider,
                model=resolved_model,
                runtime_profile=resolved_runtime_profile,
                llm_scope=llm_scope,
                output_format_profile={},
                reason="provider_blocked_by_local_only_policy",
                chain={},
            ).as_dict()

        fmt_profile = get_model_output_format_profile_service().resolve(
            planning_policy=dict(cfg_all.get("planning_policy") or {}),
            provider=resolved_provider,
            model_name=resolved_model,
            runtime_profile_name=resolved_runtime_profile,
            run_telemetry=run_telemetry or {},
        )
        chain = get_routing_decision_service().build_decision_chain(
            cfg=cfg_all,
            task_kind=task_kind,
            requested={"provider": resolved_provider, "model": resolved_model},
            effective={"provider": resolved_provider, "model": resolved_model, "llm_scope": llm_scope},
            sources={"provider_source": "hint_routing", "model_source": "hint_routing"},
            recommendation={"selection_source": f"hint:{normalized_hint}"},
        )
        return HintRoutingDecision(
            hint=normalized_hint,
            available=True,
            provider=resolved_provider,
            model=resolved_model,
            runtime_profile=resolved_runtime_profile,
            llm_scope=llm_scope,
            output_format_profile=fmt_profile,
            reason="resolved",
            chain=chain,
        ).as_dict()


_SERVICE = HintRoutingService()


def get_hint_routing_service() -> HintRoutingService:
    return _SERVICE

