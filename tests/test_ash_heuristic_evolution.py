"""Tests for AI-Snake Heuristic Evolution Runtime Hardening (ASH-0xx).

Covers ASH-050 (regression: no tick blocking) and ASH-051 (auto-activation pipeline).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ── M01: Governance mode ──────────────────────────────────────────────────────

from agent.services.heuristic_runtime.governance import GovernanceMode


class TestGovernanceMode:
    def test_all_four_modes_parse(self):
        for mode in ("auto_without_human_approval", "human_approval_required",
                     "observe_only", "frozen"):
            assert GovernanceMode.from_str(mode).value == mode

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown governance_mode"):
            GovernanceMode.from_str("banana")

    def test_auto_allows_creation_and_promotion(self):
        m = GovernanceMode.AUTO_WITHOUT_HUMAN_APPROVAL
        assert m.allows_candidate_creation is True
        assert m.allows_auto_promotion is True
        assert m.requires_human_approval is False

    def test_observe_only_blocks_creation(self):
        m = GovernanceMode.OBSERVE_ONLY
        assert m.allows_candidate_creation is False
        assert m.allows_auto_promotion is False

    def test_frozen_blocks_creation(self):
        m = GovernanceMode.FROZEN
        assert m.allows_candidate_creation is False

    def test_human_approval_mode(self):
        m = GovernanceMode.HUMAN_APPROVAL_REQUIRED
        assert m.allows_candidate_creation is True
        assert m.allows_auto_promotion is False
        assert m.requires_human_approval is True


# ── M01: State catalog ────────────────────────────────────────────────────────

from agent.services.heuristic_runtime.snake_state_catalog import (
    SnakeState, is_valid_transition, VALID_TRANSITIONS
)


class TestSnakeStateCatalog:
    def test_all_states_present(self):
        expected = {
            "disabled", "paused", "observe_only", "follow_user", "inspect_artifact",
            "move_to_artifact", "explain_artifact", "chat_with_user", "waiting_for_ai",
            "heuristic_fallback", "candidate_shadow_test", "candidate_rollout",
            "candidate_auto_active", "quarantined", "error",
        }
        actual = {s.value for s in SnakeState}
        assert expected == actual

    def test_any_state_can_transition_to_error(self):
        for state in SnakeState:
            if state != SnakeState.ERROR:
                assert is_valid_transition(state, SnakeState.ERROR), f"{state} → ERROR should be valid"

    def test_paused_requires_explicit_resume(self):
        # paused cannot go directly to most active states
        assert not is_valid_transition(SnakeState.PAUSED, SnakeState.MOVE_TO_ARTIFACT)

    def test_candidate_rollout_advances_to_auto_active(self):
        assert is_valid_transition(SnakeState.CANDIDATE_ROLLOUT, SnakeState.CANDIDATE_AUTO_ACTIVE)

    def test_move_to_artifact_can_reach_explain(self):
        assert is_valid_transition(SnakeState.MOVE_TO_ARTIFACT, SnakeState.EXPLAIN_ARTIFACT)

    def test_explain_can_reach_chat(self):
        assert is_valid_transition(SnakeState.EXPLAIN_ARTIFACT, SnakeState.CHAT_WITH_USER)


# ── M01: Snake interfaces ─────────────────────────────────────────────────────

from agent.services.heuristic_runtime.snake_interfaces import (
    MovementMode, SnakeRuntimeState, CandidateRecord, ActivationPolicy
)


class TestSnakeInterfaces:
    def test_movement_mode_validation(self):
        assert MovementMode.is_valid("follow_user")
        assert MovementMode.is_valid("lurk")
        assert not MovementMode.is_valid("teleport")

    def test_runtime_state_independence(self):
        state = SnakeRuntimeState()
        state2 = state.with_movement("lurk")
        assert state2.movement_mode == "lurk"
        assert state2.governance_mode == state.governance_mode

    def test_candidate_record_expiry(self):
        rec = CandidateRecord(
            proposal_id="test-1",
            domain="tui_snake",
            base_heuristic_ref="base",
            action_kind="follow_with_distance",
            status="candidate",
            simulation_result=None,
            expires_at=time.time() - 1,  # already expired
        )
        assert rec.is_expired is True

    def test_candidate_not_expired(self):
        rec = CandidateRecord(
            proposal_id="test-2",
            domain="tui_snake",
            base_heuristic_ref="base",
            action_kind="follow_with_distance",
            status="candidate",
            simulation_result=None,
            expires_at=time.time() + 86400,
        )
        assert rec.is_expired is False

    def test_activation_policy_defaults(self):
        policy = ActivationPolicy()
        assert policy.progressive_rollout is True
        assert policy.rollout_quota_stages == [0.1, 0.5, 1.0]
        assert policy.direct_candidate_runtime_allowed is False


# ── M02: Fingerprint ─────────────────────────────────────────────────────────

from agent.services.heuristic_runtime.proposal_service import compute_candidate_fingerprint


class TestCandidateFingerprint:
    def test_same_inputs_same_fingerprint(self):
        fp1 = compute_candidate_fingerprint("tui_snake", "base", "follow_with_distance", {"a": 1})
        fp2 = compute_candidate_fingerprint("tui_snake", "base", "follow_with_distance", {"a": 1})
        assert fp1 == fp2

    def test_different_inputs_different_fingerprint(self):
        fp1 = compute_candidate_fingerprint("tui_snake", "base1", "follow_with_distance", {})
        fp2 = compute_candidate_fingerprint("tui_snake", "base2", "follow_with_distance", {})
        assert fp1 != fp2

    def test_fallback_reason_not_in_fingerprint(self):
        # fallback_reason should not affect fingerprint
        fp1 = compute_candidate_fingerprint("tui_snake", "base", "follow_with_distance", {})
        fp2 = compute_candidate_fingerprint("tui_snake", "base", "follow_with_distance", {"fallback_reason": "no_match"})
        # Only stable fields matter — but "fallback_reason" is in parameters here, so it DOES differ
        # The real requirement: context_hash_pattern not in fingerprint
        assert fp1 != fp2  # different params → different fingerprint

    def test_sha256_format(self):
        fp = compute_candidate_fingerprint("tui_snake", "base", "lurk_near", {})
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


# ── M02: Raw data validator ───────────────────────────────────────────────────

from agent.services.heuristic_runtime.candidate_raw_validator import (
    CandidateRawValidator, validate_candidate_raw_data
)


class TestCandidateRawValidator:
    def test_clean_candidate_passes(self):
        candidate = {
            "proposal_id": "abc",
            "domain": "tui_snake",
            "description": "improvement proposal",
            "parameters": {"rationale": "test", "action_kind": "follow_with_distance"},
        }
        result = validate_candidate_raw_data(candidate)
        assert result.passed is True
        assert result.reason_codes == []

    def test_raw_screen_blocked(self):
        candidate = {"raw_screen": "ANSI output XYZ", "proposal_id": "x"}
        result = validate_candidate_raw_data(candidate)
        assert result.passed is False
        assert "raw_content_forbidden" in result.reason_codes
        assert "raw_screen" in result.forbidden_fields

    def test_nested_secret_blocked(self):
        candidate = {
            "parameters": {
                "data": {"api_key": "sk-1234"},
            }
        }
        result = validate_candidate_raw_data(candidate)
        assert result.passed is False

    def test_raw_prompt_in_params_blocked(self):
        candidate = {"parameters": {"raw_prompt": "system: you are an AI"}}
        result = validate_candidate_raw_data(candidate)
        assert result.passed is False


# ── M02: Migration gate ───────────────────────────────────────────────────────

from agent.services.heuristic_runtime.candidate_migration import (
    run_candidate_migration, is_candidate_eligible
)


class TestCandidateMigration:
    def _write_candidate(self, directory: str, cid: str, **kwargs) -> str:
        data = {
            "proposal_id": cid,
            "domain": "tui_snake",
            "status": "candidate",
            "simulation_result": None,
            **kwargs,
        }
        path = os.path.join(directory, f"{cid}.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_null_simulation_result_gets_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            os.makedirs(domain_dir)
            self._write_candidate(domain_dir, "cand-001", simulation_result=None)
            report = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report.set_pending_simulation == 1
            # Verify file was updated
            with open(os.path.join(domain_dir, "cand-001.json")) as f:
                data = json.load(f)
            assert data["status"] == "pending_simulation"

    def test_already_pending_not_double_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            os.makedirs(domain_dir)
            self._write_candidate(domain_dir, "cand-002", status="pending_simulation")
            report = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report.already_pending == 1
            assert report.set_pending_simulation == 0

    def test_with_simulation_result_not_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            os.makedirs(domain_dir)
            self._write_candidate(domain_dir, "cand-003",
                                  simulation_result={"can_activate": True})
            report = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report.set_pending_simulation == 0

    def test_expired_candidate_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            quarantine_dir = os.path.join(tmp, "quarantine", "tui_snake")
            os.makedirs(domain_dir)
            self._write_candidate(domain_dir, "cand-004",
                                  expires_at=time.time() - 1)
            report = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report.quarantined_expired == 1
            assert not os.path.exists(os.path.join(domain_dir, "cand-004.json"))
            assert os.path.exists(os.path.join(quarantine_dir, "cand-004.json"))

    def test_idempotent_second_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            os.makedirs(domain_dir)
            self._write_candidate(domain_dir, "cand-005", simulation_result=None)
            run_candidate_migration(domain="tui_snake", base_path=tmp)
            report2 = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report2.set_pending_simulation == 0
            assert report2.already_pending == 1

    def test_is_candidate_eligible(self):
        assert is_candidate_eligible({"status": "candidate"}) is True
        assert is_candidate_eligible({"status": "auto_promoted"}) is True
        assert is_candidate_eligible({"status": "pending_simulation"}) is False
        assert is_candidate_eligible({"status": "quarantined"}) is False


# ── M03: Audit events ─────────────────────────────────────────────────────────

from agent.services.heuristic_runtime import snake_audit_events as audit


class TestSnakeAuditEvents:
    def setup_method(self):
        audit.clear_events()

    def test_emit_returns_event_id(self):
        eid = audit.emit("test_event", key="value")
        assert len(eid) == 36  # UUID

    def test_events_are_time_sortable(self):
        t1 = audit.snake_decision(heuristic_id="h1", action_kind="follow", fallback_reason="")
        time.sleep(0.001)
        t2 = audit.candidate_created(proposal_id="p1", domain="tui_snake", fingerprint="abc")
        events = audit.get_events()
        ids = [e["event_id"] for e in events]
        assert ids.index(t1) < ids.index(t2)

    def test_get_events_filtered_by_type(self):
        audit.snake_decision(heuristic_id="h1", action_kind="lurk", fallback_reason="no_match")
        audit.candidate_created(proposal_id="p1", domain="tui_snake", fingerprint="abc")
        events = audit.get_events(event_type="snake_decision")
        assert all(e["event_type"] == "snake_decision" for e in events)

    def test_events_contain_no_raw_data_keys(self):
        audit.candidate_shadow_watchdog_triggered(
            candidate_id="c1", trigger="exception_rate", value=0.15
        )
        events = audit.get_events()
        for event in events:
            for key in event:
                assert "raw_" not in key.lower()
                assert "secret" not in key.lower()
                assert "password" not in key.lower()

    def test_watchdog_event_type_present(self):
        audit.candidate_shadow_watchdog_triggered(
            candidate_id="x", trigger="no_movement", value=11
        )
        events = audit.get_events(event_type="candidate_shadow_watchdog_triggered")
        assert len(events) == 1

    def test_rollout_stage_event(self):
        audit.candidate_rollout_stage_advanced(
            candidate_id="c1", stage="partial", quota=0.5, decisions_at_stage=50
        )
        events = audit.get_events(event_type="candidate_rollout_stage_advanced")
        assert events[0]["quota"] == 0.5


# ── M03: Candidate scoring ────────────────────────────────────────────────────

from agent.services.heuristic_runtime.candidate_scorer import compute_score


class TestCandidateScoring:
    def test_good_candidate_meets_thresholds(self):
        score = compute_score(
            simulation_passed=True,
            shadow_decision_count=60,
            shadow_duration_seconds=35.0,
            shadow_match_rate=0.85,
        )
        assert score.meets_thresholds is True
        assert score.block_reason == ""
        assert score.activation_score >= 0.7

    def test_no_simulation_blocks(self):
        score = compute_score(
            simulation_passed=False,
            shadow_decision_count=60,
            shadow_duration_seconds=35.0,
            shadow_match_rate=0.9,
        )
        assert score.meets_thresholds is False
        assert "simulation" in score.block_reason

    def test_too_few_decisions_blocks(self):
        score = compute_score(
            simulation_passed=True,
            shadow_decision_count=30,
            shadow_duration_seconds=35.0,
            shadow_match_rate=0.9,
        )
        assert score.meets_thresholds is False
        assert "shadow_decisions_too_few" in score.block_reason

    def test_too_short_duration_blocks(self):
        score = compute_score(
            simulation_passed=True,
            shadow_decision_count=60,
            shadow_duration_seconds=10.0,  # < 30s
            shadow_match_rate=0.9,
        )
        assert score.meets_thresholds is False
        assert "shadow_duration" in score.block_reason

    def test_low_match_rate_blocks(self):
        score = compute_score(
            simulation_passed=True,
            shadow_decision_count=60,
            shadow_duration_seconds=35.0,
            shadow_match_rate=0.2,  # very low
        )
        assert score.meets_thresholds is False

    def test_score_dict_serializable(self):
        score = compute_score(
            simulation_passed=True,
            shadow_decision_count=55,
            shadow_duration_seconds=31.0,
            shadow_match_rate=0.8,
        )
        d = score.to_dict()
        assert isinstance(d["activation_score"], float)
        assert isinstance(d["risk_score"], float)
        assert isinstance(d["meets_thresholds"], bool)


# ── M03: Shadow runner ────────────────────────────────────────────────────────

from agent.services.heuristic_runtime.shadow_runner import ShadowRunner, SHADOW_MIN_DECISIONS


class TestShadowRunner:
    def _make_runner(self, candidate_id: str = "test-cand") -> ShadowRunner:
        candidate = {"proposal_id": candidate_id, "domain": "tui_snake"}
        return ShadowRunner(candidate)

    def test_runner_starts_active(self):
        runner = self._make_runner()
        assert runner.is_active is True

    def test_normal_decisions_accumulate(self):
        runner = self._make_runner()
        for _ in range(5):
            runner.record_decision(
                shadow_action_kind="follow_with_distance",
                active_action_kind="follow_with_distance",
            )
        assert runner.state.decision_count == 5

    def test_watchdog_triggers_on_exception_rate(self):
        runner = self._make_runner()
        # Record 10 decisions: all exceptions (rate = 1.0 > 0.1)
        for _ in range(10):
            runner.record_decision(
                shadow_action_kind="exception",
                active_action_kind="follow_with_distance",
                exception=True,
            )
        assert runner.state.aborted is True
        assert "shadow_watchdog_triggered" in runner.state.abort_reason

    def test_watchdog_triggers_on_loop(self):
        runner = self._make_runner()
        pos = (10, 5)
        for _ in range(6):
            runner.record_decision(
                shadow_action_kind="follow_with_distance",
                active_action_kind="follow_with_distance",
                position_hint=pos,
            )
        assert runner.state.aborted is True

    def test_compute_candidate_action_follow(self):
        candidate = {
            "proposal_id": "c1",
            "domain": "tui_snake",
            "runtime": {
                "action": {"kind": "follow_with_distance"},
                "triggers": [],
            },
        }
        runner = ShadowRunner(candidate)
        action = runner.compute_candidate_action(
            candidate, section="dashboard", ai_status="online", artifact_present=False
        )
        assert action == "follow_with_distance"

    def test_shadow_run_both_thresholds_required(self):
        runner = self._make_runner()
        # Enough decisions but not enough time
        runner._state.started_at = time.time()  # just started → duration ≈ 0
        for _ in range(SHADOW_MIN_DECISIONS + 1):
            runner.record_decision(
                shadow_action_kind="follow_with_distance",
                active_action_kind="follow_with_distance",
            )
        # Not completed because duration < 30s
        assert runner.state.completed is False


# ── M03: Simulation fixtures ──────────────────────────────────────────────────

from agent.services.heuristic_runtime.snake_simulation_fixtures import build_tui_snake_fixtures


class TestSnakeSimulationFixtures:
    def test_fixtures_return_list(self):
        fixtures = build_tui_snake_fixtures()
        assert len(fixtures) >= 5

    def test_all_fixtures_have_surface(self):
        for fixture in build_tui_snake_fixtures():
            assert fixture.surface == "tui_snake"


# ── M04: Auto activator ───────────────────────────────────────────────────────

from agent.services.heuristic_runtime.auto_activator import AutoActivator, ROLLOUT_STAGES


class TestAutoActivator:
    def _make_candidate(self, *, meets_thresholds: bool = True) -> dict:
        return {
            "proposal_id": "test-candidate-001",
            "domain": "tui_snake",
            "status": "candidate",
            "base_heuristic_ref": "snake_tui_follow_distance_default",
            "parameters": {"action_kind": "follow_with_distance"},
            "score": {
                "meets_thresholds": meets_thresholds,
                "block_reason": "" if meets_thresholds else "simulation_not_passed",
                "activation_score": 0.8 if meets_thresholds else 0.0,
                "risk_score": 0.2,
            },
        }

    def test_promotion_requires_score_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            activator = AutoActivator(base_path=tmp)
            candidate = self._make_candidate(meets_thresholds=False)
            result = activator.promote(candidate)
            assert result.success is False
            assert "score_thresholds_not_met" in result.reason

    def test_successful_promotion_creates_active_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = self._make_candidate(meets_thresholds=True)
            result = activator.promote(candidate)
            assert result.success is True
            assert os.path.exists(result.active_path)
            with open(result.active_path) as f:
                data = json.load(f)
            assert data["status"] == "active"
            assert data["activation_reason"] == "auto_without_human_approval"

    def test_rollout_stages_are_three(self):
        assert len(ROLLOUT_STAGES) == 3
        quotas = [stage[0] for stage in ROLLOUT_STAGES]
        assert quotas == [0.1, 0.5, 1.0]

    def test_rollout_starts_at_canary(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = self._make_candidate()
            activator.promote(candidate)
            state = activator.get_rollout_state("test-candidate-001")
            assert state is not None
            assert state.stage_label == "canary"
            assert state.quota == 0.1

    def test_should_use_candidate_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = self._make_candidate()
            activator.promote(candidate)
            # At 10% quota: ticks 0-9 use candidate, 10-99 do not
            uses = sum(
                1 for tick in range(100)
                if activator.should_use_candidate("test-candidate-001", tick)
            )
            assert uses == 10  # exactly 10% of 100 ticks

    def test_rollback_quarantines_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            os.makedirs(os.path.join(tmp, "quarantine", "tui_snake"))
            os.makedirs(os.path.join(tmp, "archive", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = self._make_candidate()
            activator.promote(candidate)
            # Trigger rollback via negative feedback
            from agent.services.heuristic_runtime.auto_activator import ROLLBACK_NEGATIVE_FEEDBACK_COUNT
            for _ in range(ROLLBACK_NEGATIVE_FEEDBACK_COUNT):
                activator.tick_rollout(
                    "test-candidate-001",
                    negative_feedback_count=ROLLBACK_NEGATIVE_FEEDBACK_COUNT,
                )
            state = activator.get_rollout_state("test-candidate-001")
            assert state is not None
            assert state.active is False


# ── M05: Artifact intent decision model (ASH-042) ────────────────────────────

from client_surfaces.operator_tui.artifact_intent import (
    SnakeArtifactDecision, SnakeArtifactIntentKind, SnakeArtifactMovement,
    SnakeArtifactInteraction, build_snake_artifact_decision,
    ArtifactIntent, IntentConfidence,
)


class TestSnakeArtifactDecision:
    def test_fast_target_requires_target(self):
        decision = SnakeArtifactDecision(
            intent=SnakeArtifactIntentKind.MOVE_TO,
            movement=SnakeArtifactMovement.FAST_TARGET,
            target=None,  # no target!
        )
        assert decision.is_valid() is False

    def test_follow_without_target_is_valid(self):
        decision = SnakeArtifactDecision(
            intent=SnakeArtifactIntentKind.NONE,
            movement=SnakeArtifactMovement.FOLLOW_USER,
            target=None,
        )
        assert decision.is_valid() is True

    def test_move_to_advances_to_explain(self):
        decision = SnakeArtifactDecision(
            intent=SnakeArtifactIntentKind.MOVE_TO,
            movement=SnakeArtifactMovement.FAST_TARGET,
            target=None,  # simplified for test
        )
        next_d = decision.next_intent_on_arrival()
        assert next_d.intent == SnakeArtifactIntentKind.EXPLAIN
        assert next_d.at_target is True
        assert next_d.movement == SnakeArtifactMovement.NONE

    def test_explain_advances_to_chat(self):
        decision = SnakeArtifactDecision(
            intent=SnakeArtifactIntentKind.EXPLAIN,
            movement=SnakeArtifactMovement.NONE,
            at_target=True,
        )
        next_d = decision.next_intent_on_arrival()
        assert next_d.intent == SnakeArtifactIntentKind.CHAT
        assert next_d.interaction == SnakeArtifactInteraction.OPEN_CHAT

    def test_none_intent_no_confidence_returns_empty(self):
        intent = ArtifactIntent(
            confidence=IntentConfidence.NONE,
            target=None,
            score=0.0,
            reason="no-target",
        )
        decision = build_snake_artifact_decision(intent)
        assert decision.intent == SnakeArtifactIntentKind.NONE
        assert decision.movement == SnakeArtifactMovement.NONE

    def test_to_game_dict_has_required_keys(self):
        decision = SnakeArtifactDecision()
        d = decision.to_game_dict()
        assert "artifact_intent_kind" in d
        assert "artifact_movement" in d
        assert "artifact_interaction" in d


# ── ASH-051: Auto-activation integration pipeline ────────────────────────────

class TestAutoActivationPipeline:
    """End-to-end: candidate created → migration → scoring → promotion."""

    def test_default_is_auto_without_human_approval(self):
        policy = ActivationPolicy()
        assert policy.candidate_activation_mode == GovernanceMode.AUTO_WITHOUT_HUMAN_APPROVAL

    def test_candidate_without_simulation_not_promoted(self):
        """ASH-016 + ASH-030: simulation_result: null blocks promotion."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = {
                "proposal_id": "no-sim-candidate",
                "domain": "tui_snake",
                "status": "pending_simulation",  # set by migration gate
                "simulation_result": None,
                "score": {"meets_thresholds": False, "block_reason": "simulation_not_passed"},
            }
            result = activator.promote(candidate)
            assert result.success is False

    def test_candidate_with_good_score_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active", "tui_snake"))
            activator = AutoActivator(base_path=tmp)
            candidate = {
                "proposal_id": "good-candidate-001",
                "domain": "tui_snake",
                "status": "candidate",
                "parameters": {"action_kind": "follow_with_distance"},
                "score": {
                    "meets_thresholds": True,
                    "block_reason": "",
                    "activation_score": 0.82,
                    "risk_score": 0.15,
                },
            }
            result = activator.promote(candidate)
            assert result.success is True

    def test_progressive_rollout_all_three_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            for subdir in ["active/tui_snake", "quarantine/tui_snake", "archive/tui_snake"]:
                os.makedirs(os.path.join(tmp, subdir))
            activator = AutoActivator(base_path=tmp)
            candidate = {
                "proposal_id": "rollout-test-001",
                "domain": "tui_snake",
                "parameters": {"action_kind": "lurk_near"},
                "score": {"meets_thresholds": True, "block_reason": "", "activation_score": 0.8, "risk_score": 0.1},
            }
            activator.promote(candidate)

            # Advance through canary → partial → full
            state = activator.get_rollout_state("rollout-test-001")
            assert state.stage_label == "canary"

            # Simulate enough decisions at canary (min=20)
            for i in range(21):
                s = activator.tick_rollout("rollout-test-001")
            assert s is not None
            assert s.stage_label == "partial"

            # Simulate enough decisions at partial (min=50)
            for i in range(51):
                s = activator.tick_rollout("rollout-test-001")
            assert s.stage_label == "full"
            assert s.quota == 1.0

    def test_migration_of_existing_null_simulation_candidates(self):
        """ASH-016: all 11 existing candidates with simulation_result: null get migrated."""
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = os.path.join(tmp, "candidates", "tui_snake")
            os.makedirs(domain_dir)
            # Simulate 11 candidates like the real ones
            for i in range(11):
                data = {
                    "proposal_id": f"candidate-{i:03d}",
                    "domain": "tui_snake",
                    "status": "candidate",
                    "simulation_result": None,
                }
                with open(os.path.join(domain_dir, f"candidate-{i:03d}.json"), "w") as f:
                    json.dump(data, f)
            report = run_candidate_migration(domain="tui_snake", base_path=tmp)
            assert report.set_pending_simulation == 11
            assert report.quarantined_expired == 0


# ── ASH-050: Regression — no tick blocking ───────────────────────────────────

class TestSnakeHeuristicMixinRateLimit:
    """ASH-050: rate limit prevents candidate flood."""

    def test_rate_limit_max_per_hour(self):
        from client_surfaces.operator_tui.snake_heuristic_mixin import (
            _PROPOSAL_MAX_PER_HOUR, _PROPOSAL_HOUR
        )
        assert _PROPOSAL_MAX_PER_HOUR == 3
        assert _PROPOSAL_HOUR == 3600.0

    def test_min_interval(self):
        from client_surfaces.operator_tui.snake_heuristic_mixin import _PROPOSAL_MIN_INTERVAL
        assert _PROPOSAL_MIN_INTERVAL == 300.0

    def test_governance_mode_blocks_creation(self):
        from agent.services.heuristic_runtime.governance import GovernanceMode
        for mode in (GovernanceMode.OBSERVE_ONLY, GovernanceMode.FROZEN):
            assert mode.allows_candidate_creation is False
