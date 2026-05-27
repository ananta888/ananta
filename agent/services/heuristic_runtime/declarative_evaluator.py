"""DeclarativeHeuristicEvaluator — evaluates heuristics with runtime.mode=declarative_rules.

Supports the full declarative DSL:
  triggers  → query_contains_any, query_matches_regex_safe, active_panel_is,
               selected_artifact_present, ai_status_is, event_type_is
  selection → ranked_codecompass_refs, selected_artifacts_first, active_goal_first,
               helpcenter_refs, todo_status_refs, sourcepack_technical, no_good_match
  action    → follow_with_distance, lurk_near, show_hint, show_context_summary,
               open_source_ref, ask_scope, no_action

All evaluation is deterministic and runs without any AI/LLM call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


_SAFE_REGEX_TIMEOUT_MS = 100  # ms — applies via fallback only (Python re has no timeout)
_SAFE_REGEX_MAX_LEN = 200


@dataclass
class EvalTrace:
    heuristic_id: str
    matched_triggers: list[str] = field(default_factory=list)
    selection_strategy: str = ""
    action_kind: str = ""
    reason_codes: list[str] = field(default_factory=list)


class DeclarativeEvaluatorError(ValueError):
    pass


class DeclarativeHeuristicEvaluator:
    """Evaluates a HeuristicDefinition with mode=declarative_rules against a DecisionContext."""

    def evaluate(
        self,
        hdef: HeuristicDefinition,
        ctx: DecisionContext,
        *,
        query: str = "",
    ) -> tuple[DecisionResult, EvalTrace]:
        """Return (DecisionResult, EvalTrace) for the given context.

        If no trigger matches, returns no_good_match with reason_code no_trigger_matched.
        """
        runtime = dict(hdef.parameters.get("runtime") or {})
        trace = EvalTrace(heuristic_id=hdef.heuristic_id)

        # ── Trigger evaluation ─────────────────────────────────────────────────
        triggers = list(runtime.get("triggers") or [])
        if triggers:
            matched = self._evaluate_triggers(triggers, ctx, query=query)
            if not matched:
                trace.reason_codes.append("no_trigger_matched")
                return DecisionResult.no_good_match(), trace
            trace.matched_triggers = matched

        # ── Selection ─────────────────────────────────────────────────────────
        selection = dict(runtime.get("selection") or {})
        selected_refs = self._evaluate_selection(selection, ctx)
        trace.selection_strategy = str(selection.get("strategy") or "")

        # ── Action ────────────────────────────────────────────────────────────
        action = dict(runtime.get("action") or {})
        result = self._evaluate_action(action, ctx, selected_refs=selected_refs,
                                       heuristic_id=hdef.heuristic_id, trace=trace)
        return result, trace

    # ── Trigger ───────────────────────────────────────────────────────────────

    def _evaluate_triggers(
        self,
        triggers: list[dict[str, Any]],
        ctx: DecisionContext,
        *,
        query: str,
    ) -> list[str]:
        """Return list of trigger descriptions that matched. Empty = none matched."""
        matched: list[str] = []
        q_lower = query.lower()
        recent_event_types = {
            str(e.get("event_type") or e.get("kind") or "")
            for e in (ctx.recent_events or [])
        }

        for trigger in triggers:
            desc = _trigger_desc(trigger)

            # query_contains_any
            kws = trigger.get("query_contains_any")
            if kws:
                if any(str(k).lower() in q_lower for k in kws):
                    matched.append(f"query_contains_any:{desc}")
                    continue

            # query_matches_regex_safe
            pattern_str = trigger.get("query_matches_regex_safe")
            if pattern_str:
                if _safe_regex_match(str(pattern_str), query):
                    matched.append(f"query_matches_regex:{desc}")
                    continue

            # active_panel_is
            panel = trigger.get("active_panel_is")
            if panel is not None:
                if str(ctx.active_panel or "").lower() == str(panel).lower():
                    matched.append(f"active_panel_is:{panel}")
                    continue

            # selected_artifact_present
            artifact_check = trigger.get("selected_artifact_present")
            if artifact_check is not None:
                present = bool(ctx.selected_artifacts)
                if present == bool(artifact_check):
                    matched.append("selected_artifact_present")
                    continue

            # ai_status_is
            ai_status = trigger.get("ai_status_is")
            if ai_status is not None:
                if str(ctx.ai_status or "").lower() == str(ai_status).lower():
                    matched.append(f"ai_status_is:{ai_status}")
                    continue

            # event_type_is
            event_type = trigger.get("event_type_is")
            if event_type is not None:
                if str(event_type) in recent_event_types:
                    matched.append(f"event_type_is:{event_type}")
                    continue

        return matched

    # ── Selection ─────────────────────────────────────────────────────────────

    def _evaluate_selection(
        self,
        selection: dict[str, Any],
        ctx: DecisionContext,
    ) -> list[str]:
        strategy = str(selection.get("strategy") or "")
        max_refs = int(selection.get("max_refs") or 5)

        if strategy == "selected_artifacts_first":
            refs = list(ctx.selected_artifacts or [])
            if ctx.active_goal_id:
                refs.append(f"goal:{ctx.active_goal_id}")
            return refs[:max_refs]

        if strategy == "active_goal_first":
            refs = []
            if ctx.active_goal_id:
                refs.append(f"goal:{ctx.active_goal_id}")
            if ctx.active_task_id:
                refs.append(f"task:{ctx.active_task_id}")
            refs.extend(ctx.selected_artifacts or [])
            return refs[:max_refs]

        if strategy == "helpcenter_refs":
            return [s for s in (ctx.allowed_source_scopes or [])
                    if "helpcenter" in s][:max_refs]

        if strategy == "todo_status_refs":
            return [s for s in (ctx.allowed_source_scopes or [])
                    if "todo" in s or "task" in s][:max_refs]

        if strategy in ("ranked_codecompass_refs", "sourcepack_technical"):
            refs = list(ctx.selected_artifacts or [])
            refs.extend(s for s in (ctx.allowed_source_scopes or []))
            return refs[:max_refs]

        if strategy == "no_good_match":
            return []

        # Default: return selected artifacts
        return list(ctx.selected_artifacts or [])[:max_refs]

    # ── Action ────────────────────────────────────────────────────────────────

    def _evaluate_action(
        self,
        action: dict[str, Any],
        ctx: DecisionContext,
        *,
        selected_refs: list[str],
        heuristic_id: str,
        trace: EvalTrace,
    ) -> DecisionResult:
        kind = str(action.get("kind") or "no_action")
        fallback = str(action.get("fallback") or "no_action")
        trace.action_kind = kind

        if kind == "follow_with_distance":
            distance = int(action.get("distance") or 4)
            return DecisionResult(
                action_kind="follow",
                confidence=1.0,
                source="heuristic",
                suggested_motion=SuggestedMotion(dx=1, dy=0),
                strategy_id=heuristic_id,
                reason_codes=[f"declarative:follow:distance={distance}"],
            )

        if kind == "lurk_near":
            return DecisionResult.heuristic_lurk(strategy_id=heuristic_id)

        if kind == "show_hint":
            hint = str(action.get("hint_text") or "")
            return DecisionResult(
                action_kind="explain",
                confidence=1.0,
                source="heuristic",
                strategy_id=heuristic_id,
                reason_codes=[f"declarative:show_hint"],
                answer_blocks=[{"type": "hint", "text": hint}],
            )

        if kind == "show_context_summary":
            if not selected_refs:
                if fallback == "no_good_match":
                    return DecisionResult.no_good_match()
                return DecisionResult.no_good_match()
            return DecisionResult(
                action_kind="chat",
                confidence=0.9,
                source="heuristic",
                selected_context_refs=list(selected_refs),
                strategy_id=heuristic_id,
                reason_codes=["declarative:show_context_summary"],
            )

        if kind == "open_source_ref":
            ref = selected_refs[0] if selected_refs else ""
            return DecisionResult(
                action_kind="chat",
                confidence=0.8,
                source="heuristic",
                selected_context_refs=[ref] if ref else [],
                strategy_id=heuristic_id,
                reason_codes=["declarative:open_source_ref"],
            )

        if kind == "ask_scope":
            return DecisionResult(
                action_kind="chat",
                confidence=0.7,
                source="heuristic",
                strategy_id=heuristic_id,
                reason_codes=["declarative:ask_scope"],
            )

        # no_action or unknown
        return DecisionResult.no_good_match()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trigger_desc(trigger: dict[str, Any]) -> str:
    return "|".join(f"{k}={v}" for k, v in trigger.items())[:80]


def _safe_regex_match(pattern: str, text: str) -> bool:
    if len(pattern) > _SAFE_REGEX_MAX_LEN:
        return False
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        return False
