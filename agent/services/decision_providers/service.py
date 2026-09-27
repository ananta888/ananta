"""Decision areas at runtime: config -> providers -> cascade -> mode (DPRV).

``DecisionService.decide(area, request)`` returns ``None`` when the area is off
(callers keep today's behaviour without any extra work), otherwise an
``AreaDecision`` with the cascade outcome and whether the caller may use it
(``mode == active`` and accepted). Every decision in shadow or active mode is
recorded through a sink without the decision text (only its sha256), so a
shadow run can be evaluated against today's behaviour later.

Decisions are proposals: callers keep every policy, approval and security gate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.decision_providers.base import DecisionProvider
from agent.services.decision_providers.cascade import AgreementStage, CascadeOutcome, CascadeStage, DecisionCascade
from agent.services.decision_providers.config import SECTION, area_settings
from agent.services.decision_providers.types import DecisionRequest

_log = logging.getLogger("ananta.decision_providers")


class DecisionRecordSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None: ...


class LoggingDecisionSink:
    def record(self, event: Mapping[str, Any]) -> None:
        _log.info("decision %s", json.dumps(dict(event), sort_keys=True, default=str))


@dataclass(frozen=True)
class AreaDecision:
    area: str
    mode: str
    outcome: CascadeOutcome

    @property
    def usable(self) -> bool:
        """The caller may act on it: active mode and a stage accepted it."""
        return self.mode == "active" and not self.outcome.deferred


class HubLLMCompletion:
    """The Hub's configured LLM as the completion transport of the ``llm`` provider."""

    def complete(self, prompt: str, *, timeout_seconds: float) -> tuple[str, int, int]:
        from agent.llm_integration import extract_llm_text_and_usage, generate_text

        result = generate_text(prompt, temperature=0.0, max_output_tokens=400, timeout=int(max(1, timeout_seconds)),
                               max_retries=0)
        text, usage = extract_llm_text_and_usage(result)
        return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def build_provider(name: str, settings: Mapping[str, Any], *,
                   environ: Mapping[str, str] | None = None) -> DecisionProvider:
    env = os.environ if environ is None else environ
    if name == "jev":
        from agent.services.decision_providers.typesafe import RetryPolicy, TypeSafeJevProvider, read_api_key

        key_env = str(settings.get("api_key_env") or "TYPESAFE_API_KEY")
        return TypeSafeJevProvider(
            base_url=settings["base_url"], model=settings["model"], timeout_seconds=settings["timeout_seconds"],
            retry=RetryPolicy(max_retries=int(settings["max_retries"])),
            api_key=lambda: read_api_key({"TYPESAFE_API_KEY": env.get(key_env, ""),
                                          "TYPESAFE_API_KEY_FILE": env.get(key_env + "_FILE", "")}),
            price_input_per_million=settings["price_input_per_million"],
            price_output_per_million=settings["price_output_per_million"])
    if name == "local_decision":
        from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider

        base_url = settings.get("base_url") or env.get(str(settings.get("base_url_env") or ""), "")
        return LlamaCppDecisionProvider(base_url=base_url or "http://127.0.0.1:0",
                                        timeout_seconds=settings["timeout_seconds"])
    if name == "llm":
        from agent.services.decision_providers.llm import LLMDecisionProvider

        return LLMDecisionProvider(HubLLMCompletion(), timeout_seconds=settings["timeout_seconds"])
    raise ValueError(f"decision_provider_unknown:{name}")


class DecisionService:
    def __init__(self, *, section: Callable[[], Mapping[str, Any] | None],
                 provider_factory: Callable[[str, Mapping[str, Any]], DecisionProvider] = build_provider,
                 sink: DecisionRecordSink | None = None) -> None:
        self._section = section
        self._factory = provider_factory
        self._sink = sink or LoggingDecisionSink()

    def area_mode(self, area: str) -> str:
        settings = area_settings(self._section(), area)
        return "off" if settings is None else settings["mode"]

    def area_option(self, area: str, key: str, default: Any = None) -> Any:
        settings = area_settings(self._section(), area)
        return default if settings is None else settings.get(key, default)

    def threshold(self, area: str) -> float | None:
        settings = area_settings(self._section(), area)
        return None if settings is None else float(settings["confidence_threshold"])

    def decide(self, area: str, request: DecisionRequest, *, current: Mapping[str, Any] | None = None,
               question_thresholds: Mapping[str, float] | None = None) -> AreaDecision | None:
        """The area's decision, or ``None`` when the area is off.

        ``current``: today's answer, for the record. ``question_thresholds``: caller-side per-question
        thresholds (e.g. 0 for questions whose answer only matters conditionally); configured ones win.
        """
        settings = area_settings(self._section(), area)
        if settings is None:
            return None
        thresholds = {**dict(question_thresholds or {}), **settings["question_thresholds"]}
        stages = [self._stage(entry, settings, thresholds) for entry in settings["cascade"]]
        outcome = DecisionCascade(stages, deadline_seconds=settings["deadline_seconds"]).decide(request)
        decision = AreaDecision(area, settings["mode"], outcome)
        self._record(decision, request, current)
        return decision

    def _stage(self, entry: Any, settings: Mapping[str, Any], thresholds: Mapping[str, float]) -> Any:
        if isinstance(entry, list):
            return AgreementStage([self._factory(name, settings["providers"][name]) for name in entry],
                                  settings["confidence_threshold"], thresholds)
        return CascadeStage(self._factory(entry, settings["providers"][entry]), settings["confidence_threshold"],
                            thresholds)

    def _record(self, decision: AreaDecision, request: DecisionRequest, current: Mapping[str, Any] | None) -> None:
        result = decision.outcome.result
        answers = {} if result is None else {
            key: {"choice": a.choice, "score": a.score, "probability": a.probability,
                  "confidence": round(a.confidence, 4)} for key, a in result.answers.items()}
        event = {"area": decision.area, "mode": decision.mode, "deferred": decision.outcome.deferred,
                 "disagreed": decision.outcome.disagreed,
                 "decided_by": decision.outcome.decided_by, "answers": answers,
                 "current": dict(current or {}), "usable": decision.usable,
                 "state_sha256": hashlib.sha256(request.state.encode("utf-8")).hexdigest(),
                 "trace": [{"provider": s.provider_id, "outcome": s.outcome, "reason": s.reason,
                            "latency_ms": round(s.latency_ms, 1)} for s in decision.outcome.trace],
                 "usage": None if result is None else {"input_tokens": result.usage.input_tokens,
                                                       "output_tokens": result.usage.output_tokens,
                                                       "cost_usd": result.usage.cost_usd}}
        try:
            self._sink.record(event)
        except Exception:  # noqa: BLE001 -- recording never changes a decision
            _log.debug("decision record failed", exc_info=True)


def _agent_section() -> Mapping[str, Any] | None:
    from agent.cli_backends.helpers import _get_agent_config

    return (_get_agent_config() or {}).get(SECTION)


_SERVICE: DecisionService | None = None


def get_decision_service() -> DecisionService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DecisionService(section=_agent_section)
    return _SERVICE
