"""VisualGuideDecisionService — decides whether and how a guide is triggered."""

from __future__ import annotations

import logging
import time

from agent.services.visual_guide.models import VisualGuideDecision, VisualGuideRequest
from agent.services.visual_guide.rule_engine import RuleEngine

_log = logging.getLogger(__name__)

_rule_engine = RuleEngine()


class VisualGuideDecisionService:
    """Decides whether and how a guide is triggered."""

    def decide(self, request: VisualGuideRequest, pug_settings: dict) -> VisualGuideDecision:
        """
        trigger_type=region_explain → always LLM call, priority=2
        trigger_type=ui_tick:
          - predictive_guide_enabled=False → suppressed
          - TTL not elapsed → suppressed (reason=ttl)
          - Rule engine hit → strategy=rule, no LLM
          - Otherwise → strategy=llm
        """
        decision = VisualGuideDecision(request_id=request.request_id)

        if request.trigger_type == "region_explain":
            decision.strategy = "llm"
            decision.confidence = 1.0
            decision.reason = "region_explain always uses LLM"
            return decision

        # ui_tick path
        if not pug_settings.get("predictive_guide_enabled", False):
            decision.strategy = "suppressed"
            decision.reason = "predictive_guide_enabled=False"
            return decision

        # Rule engine lookup before LLM
        route_tip = _rule_engine.lookup_route(request.route)
        if route_tip:
            decision.strategy = "rule"
            decision.confidence = 1.0
            decision.reason = f"route_rule_match:{request.route}"
            return decision

        # Score the snapshot for LLM confidence
        confidence = self._score_snapshot(
            snapshot=request.snapshot,
            prev_snapshot="",
            elapsed_ms=0.0,
            pug=pug_settings,
        )
        decision.strategy = "llm"
        decision.confidence = confidence
        decision.reason = "ui_tick_llm"
        return decision

    def _score_snapshot(
        self,
        snapshot: str,
        prev_snapshot: str,
        elapsed_ms: float,
        pug: dict,
    ) -> float:
        """Compute a confidence score 0.0-1.0 based on snapshot signals.

        Same logic as PredictionGateService in the frontend:
        - More content → higher confidence
        - Changed from previous → higher confidence
        """
        if not snapshot:
            return 0.0
        score = min(1.0, len(snapshot) / 300.0)
        if prev_snapshot and snapshot != prev_snapshot:
            score = min(1.0, score + 0.2)
        return round(score, 3)
