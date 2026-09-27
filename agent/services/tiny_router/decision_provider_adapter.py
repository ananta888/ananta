"""``TinyActionModelAdapter`` over the decision provider layer (DPRV).

Lets the tiny tool router choose tools with any configured decision provider
cascade (area ``tool_routing`` in ``decision_providers``): TypeSafe Jev, the
local ``/v1/decision`` server or an LLM, with fallback between them. The
router keeps everything it owns: its mode (disabled / shadow / active), kill
switch, risk filter and candidate validation, and the main LLM path for every
abstention. The decision area only has to be on (``shadow`` or ``active``) for
the adapter to be available; whether a candidate is used is the router's mode.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agent.services.tiny_router.types import AdapterRequest, AdapterResult, TinyActionModelProfile

ADAPTER_ID = "decision_provider"
AREA = "tool_routing"


class DecisionProviderToolAdapter:
    adapter_id = ADAPTER_ID

    def __init__(self, service: Any | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._service = service
        self._clock = clock

    def _decisions(self) -> Any:
        if self._service is None:
            from agent.services.decision_providers.service import get_decision_service

            self._service = get_decision_service()
        return self._service

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        if profile.adapter != self.adapter_id:
            return False, "adapter_profile_mismatch"
        if self._decisions().area_mode(AREA) == "off":
            return False, "decision_area_off"
        return True, "decision_provider_available"

    def propose(self, request: AdapterRequest) -> AdapterResult:
        from agent.services.decision_providers.tool_choice import TOOL_KEY, read_tool_choice, tool_questions
        from ananta_contracts.tool_decision import ABSTAIN, ToolDecision

        started = self._clock()
        candidates = None
        if self._decisions().area_option(AREA, "text_candidates", False):
            from agent.services.decision_providers.argument_candidates import default_candidate_source

            candidates = default_candidate_source()
        schema, decision_request, values = tool_questions(request.tools, request.prompt, purpose=AREA,
                                                          candidates=candidates)
        # argument questions only count for the chosen tool; read_tool_choice checks those against the threshold
        decision = self._decisions().decide(AREA, decision_request,
                                            question_thresholds={key: 0.0 for key in values if key != TOOL_KEY})
        if decision is None or decision.outcome.deferred:
            reason = "decision_area_off" if decision is None else "decision_deferred"
            payload = ToolDecision(ABSTAIN, None, reason=reason).router_payload()
        else:
            threshold = self._decisions().threshold(AREA) or request.profile.min_confidence
            from agent.services.decision_providers.tool_choice import TEXT_MIN_CONFIDENCE

            text_threshold = self._decisions().area_option(AREA, "text_min_confidence", TEXT_MIN_CONFIDENCE)
            payload = read_tool_choice(schema, decision.outcome.result, values, request.prompt,
                                       min_confidence=threshold, text_min_confidence=text_threshold).router_payload()
            payload["decided_by"] = decision.outcome.decided_by
        return AdapterResult("candidate", payload, "adapter_completed", (self._clock() - started) * 1000.0)
