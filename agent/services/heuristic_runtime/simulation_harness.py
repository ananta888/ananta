"""HeuristicSimulationHarness — runs a candidate heuristic against fixture data.

Fixture types: snake_event_sequence, chat_query_sequence, context_snapshot.
SimulationReport includes: success_rate, no_match_rate, wrong_context_rate,
avg_latency_ms, expired_usage_count, policy_violations, can_activate.

Candidate with policy_violations > 0 → can_activate=False.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.chain import RuleChain
from agent.services.heuristic_runtime.chat_selectors import build_chat_selector_chain
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition
from agent.services.heuristic_runtime.snake_rules import build_snake_rule_chain
from agent.services.heuristic_runtime.strategy import decide_for_context


# ── Fixtures ──────────────────────────────────────────────────────────────────

@dataclass
class SimulationFixture:
    fixture_type: str  # snake_event_sequence | chat_query_sequence | context_snapshot
    surface: str
    events: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    context_snapshot: dict[str, Any] | None = None
    expected_action_kind: str | None = None  # for correctness check

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_type": self.fixture_type,
            "surface": self.surface,
            "events": self.events,
            "queries": self.queries,
            "expected_action_kind": self.expected_action_kind,
        }


# ── Report ────────────────────────────────────────────────────────────────────

@dataclass
class SimulationReport:
    candidate_id: str
    candidate_version: str
    total_runs: int
    success_count: int
    no_match_count: int
    wrong_context_count: int
    policy_violation_count: int
    expired_usage_count: int
    total_latency_ms: float
    run_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_runs if self.total_runs else 0.0

    @property
    def no_match_rate(self) -> float:
        return self.no_match_count / self.total_runs if self.total_runs else 0.0

    @property
    def wrong_context_rate(self) -> float:
        return self.wrong_context_count / self.total_runs if self.total_runs else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_runs if self.total_runs else 0.0

    @property
    def can_activate(self) -> bool:
        return self.policy_violation_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "total_runs": self.total_runs,
            "success_rate": round(self.success_rate, 4),
            "no_match_rate": round(self.no_match_rate, 4),
            "wrong_context_rate": round(self.wrong_context_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "expired_usage_count": self.expired_usage_count,
            "policy_violations": self.policy_violation_count,
            "can_activate": self.can_activate,
        }


# ── Harness ───────────────────────────────────────────────────────────────────

class HeuristicSimulationHarness:
    def simulate(
        self,
        candidate: HeuristicDefinition,
        fixtures: list[SimulationFixture],
    ) -> SimulationReport:
        total = 0
        successes = 0
        no_matches = 0
        wrong_contexts = 0
        policy_violations = 0
        expired_usage = 0
        total_latency = 0.0
        details: list[dict[str, Any]] = []

        # Pre-check capability violations
        cap_violations = candidate.has_capability_violation()
        if cap_violations:
            policy_violations += len(cap_violations)

        for fixture in fixtures:
            runs = self._expand_fixture(fixture)
            for ctx, expected in runs:
                t0 = time.time()
                result = self._run_candidate(candidate, ctx)
                elapsed_ms = (time.time() - t0) * 1000
                total_latency += elapsed_ms
                total += 1

                is_no_match = result.is_no_good_match()
                is_wrong = (expected is not None and result.action_kind != expected and not is_no_match)

                if is_no_match:
                    no_matches += 1
                elif is_wrong:
                    wrong_contexts += 1
                else:
                    successes += 1

                # Policy check: policy_denied or capability violation in result
                if result.action_kind == "policy_denied":
                    policy_violations += 1
                if any("capability_violation" in rc for rc in result.reason_codes):
                    policy_violations += 1

                details.append({
                    "surface": ctx.source_surface,
                    "action_kind": result.action_kind,
                    "confidence": result.confidence,
                    "expected": expected,
                    "is_no_match": is_no_match,
                    "is_wrong": is_wrong,
                    "latency_ms": round(elapsed_ms, 2),
                })

        return SimulationReport(
            candidate_id=candidate.heuristic_id,
            candidate_version=candidate.version,
            total_runs=total,
            success_count=successes,
            no_match_count=no_matches,
            wrong_context_count=wrong_contexts,
            policy_violation_count=policy_violations,
            expired_usage_count=expired_usage,
            total_latency_ms=total_latency,
            run_details=details,
        )

    def _expand_fixture(self, fixture: SimulationFixture) -> list[tuple[DecisionContext, str | None]]:
        runs: list[tuple[DecisionContext, str | None]] = []
        snap = fixture.context_snapshot or {}

        if fixture.fixture_type == "snake_event_sequence":
            for ev in fixture.events:
                ctx = DecisionContext(
                    source_surface=fixture.surface,
                    ai_status="offline",
                    active_goal_id=snap.get("active_goal_id"),
                    active_panel=snap.get("active_panel"),
                    recent_events=[ev],
                )
                runs.append((ctx, fixture.expected_action_kind))

        elif fixture.fixture_type == "chat_query_sequence":
            for query in fixture.queries:
                ctx = DecisionContext(
                    source_surface=fixture.surface,
                    ai_status="offline",
                    selected_artifacts=snap.get("selected_artifacts", []),
                    active_goal_id=snap.get("active_goal_id"),
                    recent_events=[{
                        "kind": "chat_message",
                        "normalized_value": query[:200],
                        "timestamp": time.time(),
                    }],
                )
                runs.append((ctx, fixture.expected_action_kind))

        elif fixture.fixture_type == "context_snapshot":
            ctx = DecisionContext(
                source_surface=fixture.surface,
                ai_status=snap.get("ai_status", "offline"),
                active_goal_id=snap.get("active_goal_id"),
                selected_artifacts=snap.get("selected_artifacts", []),
                active_panel=snap.get("active_panel"),
            )
            runs.append((ctx, fixture.expected_action_kind))

        return runs

    def _run_candidate(self, candidate: HeuristicDefinition, ctx: DecisionContext) -> DecisionResult:
        """Run the candidate in isolation — no worker, no DB."""
        return decide_for_context(ctx, [candidate])
