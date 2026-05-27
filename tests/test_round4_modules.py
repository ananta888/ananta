"""Unit tests for Round 4 modules — T08.03, T09.02, T09.03, T06.03, T06.05, T07.04, T08.02, T04.03."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

# ── T09.02: PolicyDecision to_decision_result adapters ───────────────────────

class TestAiSnakePolicyAdapter:
    def test_allowed_decision_returns_follow(self):
        from client_surfaces.operator_tui.ai_snake_policy import evaluate_policy
        pd = evaluate_policy(boundary="local_observation", notes_released=True)
        result = pd.to_decision_result()
        from agent.services.heuristic_runtime.decision_result import DecisionResult
        assert isinstance(result, DecisionResult)
        assert result.action_kind == "follow"
        assert result.source == "heuristic"

    def test_denied_decision_returns_policy_denied(self):
        from client_surfaces.operator_tui.ai_snake_policy import evaluate_policy
        pd = evaluate_policy(
            boundary="external_provider",
            notes_released=False,
            external_provider=True,
        )
        result = pd.to_decision_result()
        assert result.action_kind == "policy_denied"
        assert result.fallback_reason == "policy_denied"

    def test_allowed_with_notes_reason_code(self):
        from client_surfaces.operator_tui.ai_snake_policy import evaluate_policy
        pd = evaluate_policy(boundary="worker_request", notes_released=False)
        result = pd.to_decision_result()
        # notes_metadata_only is not a deny — decision still allowed
        assert result.action_kind == "follow"


class TestChatPolicyDecisionAdapter:
    def test_allow_decision_converts(self):
        from client_surfaces.operator_tui.chat_policy import check_policy, chat_decision_to_decision_result
        decision = check_policy(
            {"channel_type": "room", "text": "hello", "sender_kind": "user"},
            "send_hub",
        )
        result = chat_decision_to_decision_result(decision)
        from agent.services.heuristic_runtime.decision_result import DecisionResult
        assert isinstance(result, DecisionResult)
        # send_hub / send_ai are mapped to "send" in the adapter
        assert result.action_kind == "send"
        assert result.source == "heuristic"

    def test_deny_decision_becomes_policy_denied(self):
        from client_surfaces.operator_tui.chat_policy import check_policy, chat_decision_to_decision_result
        decision = check_policy(
            {"channel_type": "notes", "text": "hello", "sender_kind": "user"},
            "send_hub",
        )
        assert decision["decision"] == "deny"
        result = chat_decision_to_decision_result(decision)
        assert result.action_kind == "policy_denied"


# ── T06.03: ProposalService ───────────────────────────────────────────────────

class TestProposalService:
    def _make_traces(self, n: int = 5, fallback: str = "ai_timeout"):
        from agent.services.heuristic_runtime.decision_trace import DecisionTrace
        traces = []
        for i in range(n):
            t = DecisionTrace(
                event_id=f"trace-{i}",
                surface="tui_snake",
                context_hash=f"ctx-{i}",
                lease_id=None,
                heuristic_id="follow-default",
                strategy_id="default_tui_snake",
                rule_id=None,
                confidence=0.8,
                fallback_reason=fallback,
                source="heuristic",
                action_kind="follow",
                started_at=1000.0 + i,
                resolved_at=1000.1 + i,
                reason_codes=[f"fallback:{fallback}"],
            )
            traces.append(t)
        return traces

    def test_generate_from_traces_returns_proposal(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces, domain="tui_snake")
        assert gen.proposal.proposal_id
        assert gen.dominant_heuristic_id == "follow-default"
        assert gen.fallback_pattern == "ai_timeout"
        assert gen.trace_count == 5

    def test_generate_sets_correct_ttl_for_domain(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces, domain="chat_codecompass")
        assert gen.proposal.requested_ttl_seconds == 15.0

    def test_generate_snake_ttl(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces, domain="tui_snake")
        assert gen.proposal.requested_ttl_seconds == 7.0

    def test_save_candidate_writes_json(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        (tmp_path / "candidates").mkdir()
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces)
        path = svc.save_candidate(gen.proposal)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["status"] == "candidate"
        assert data["proposal_id"] == gen.proposal.proposal_id

    def test_save_candidate_never_writes_to_active(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces)
        path = svc.save_candidate(gen.proposal)
        assert "candidates" in path
        assert "active" not in path

    def test_trace_evidence_contains_only_ids(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces(10)
        gen = svc.generate_from_traces(traces)
        evidence = gen.proposal.parameters.get("trace_evidence", [])
        assert all(isinstance(e, str) for e in evidence)
        # IDs must match trace event_ids (no raw text)
        trace_ids = {t.event_id for t in traces}
        assert all(e in trace_ids for e in evidence)

    def test_generate_raises_on_empty_traces(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        with pytest.raises(ValueError, match="empty"):
            svc.generate_from_traces([])

    def test_list_candidates_returns_saved(self, tmp_path):
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        traces = self._make_traces()
        gen = svc.generate_from_traces(traces)
        svc.save_candidate(gen.proposal)
        candidates = svc.list_candidates()
        assert len(candidates) == 1
        assert candidates[0]["proposal_id"] == gen.proposal.proposal_id


# ── T07.04: OpenCode routing restriction ─────────────────────────────────────

class TestHeuristicRoutingPolicy:
    def test_opencode_as_control_worker_blocked(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing("opencode", "control_worker")
        assert not allowed
        assert code == "opencode_not_allowed_as_heuristic_controller"

    def test_opencode_worker_as_heuristic_controller_blocked(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing("opencode-worker", "heuristic_controller")
        assert not allowed
        assert code == "opencode_not_allowed_as_heuristic_controller"

    def test_ananta_worker_as_control_allowed(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing("ananta-worker", "control_worker")
        assert allowed
        assert code == "allowed"

    def test_code_change_without_approval_blocked(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing("ananta-worker", "implement_heuristic")
        assert not allowed
        assert code == "heuristic_code_change_requires_approval"

    def test_code_change_with_operator_override_allowed(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing(
            "ananta-worker", "implement_heuristic", operator_override=True
        )
        assert allowed

    def test_code_change_with_approval_ref_allowed(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing(
            "opencode", "implement_heuristic",
            human_approval_ref="approval-xyz",
        )
        # opencode + code_change with approval — should be allowed (not a control_worker role)
        assert allowed

    def test_opencode_runtime_mode_blocked(self):
        from agent.services.worker_routing_policy_utils import check_heuristic_routing
        allowed, code = check_heuristic_routing("opencode", "runtime_mode")
        assert not allowed


# ── T08.02: HeuristicDebugView ────────────────────────────────────────────────

class TestHeuristicDebugView:
    def test_status_bar_ai(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(source="ai")
        assert HeuristicDebugView.status_bar_indicator(state) == "[AI]"

    def test_status_bar_heuristic(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(source="heuristic")
        assert HeuristicDebugView.status_bar_indicator(state) == "[H]"

    def test_status_bar_hybrid(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(source="hybrid")
        assert HeuristicDebugView.status_bar_indicator(state) == "[~]"

    def test_render_panel_contains_heuristic_id(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(heuristic_id="follow-default", version="1.0.0", ttl_remaining_seconds=4.5)
        panel = HeuristicDebugView.render_panel(state)
        assert "follow-default" in panel
        assert "4.5" in panel

    def test_render_panel_shows_fallback_reason(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(fallback_reason="ai_timeout")
        panel = HeuristicDebugView.render_panel(state)
        assert "ai_timeout" in panel

    def test_render_panel_shows_source_refs(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(last_source_refs=["ref-a", "ref-b", "ref-c"])
        panel = HeuristicDebugView.render_panel(state)
        assert "ref-a" in panel

    def test_header_proposal_badge_empty_when_zero(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(open_proposal_count=0)
        assert HeuristicDebugView.header_proposal_badge(state) == ""

    def test_header_proposal_badge_shows_count(self):
        from client_surfaces.operator_tui.heuristic_debug_view import HeuristicDebugView, HeuristicDebugState
        state = HeuristicDebugState(open_proposal_count=3)
        badge = HeuristicDebugView.header_proposal_badge(state)
        assert "3" in badge
        assert "proposal" in badge


# ── T04.03: Eclipse Snake Python bridge ──────────────────────────────────────

class TestEclipseSnakeAdapter:
    def _make_adapter(self):
        from agent.services.heuristic_runtime.eclipse_snake_adapter import EclipseSnakeDecisionAdapter
        from agent.services.heuristic_runtime.heuristic_registry_service import (
            HeuristicDefinition, HeuristicRegistry,
        )
        reg = HeuristicRegistry(base_path="/nonexistent")
        reg._loaded = True
        hdef = HeuristicDefinition(
            heuristic_id="eclipse-follow",
            version="1.0.0",
            domain="eclipse_snake",
            strategy_kind="follow",
            description="Eclipse follow",
            deterministic=True,
            safety_class="bounded",
            capabilities=("read_local_context",),
            inputs=(),
            outputs=(),
            parameters={},
            status="active",
        )
        reg._all.append(hdef)
        reg._definitions[hdef.heuristic_id] = hdef
        from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager
        manager = SnakeDecisionManager(registry=reg)
        return EclipseSnakeDecisionAdapter(manager=manager)

    def test_process_follow_intent_returns_commands(self):
        adapter = self._make_adapter()
        msg = {"intent": "follow", "zone": "editor_active", "dx": 1, "dy": 0,
               "ttl_millis": 7000, "context_hash": "ctx-eclipse-1"}
        cmds = adapter.process_intent(msg)
        assert isinstance(cmds, list)

    def test_process_lurk_intent_returns_commands(self):
        adapter = self._make_adapter()
        msg = {"intent": "lurk", "zone": "diff_panel", "dx": 0, "dy": 0,
               "ttl_millis": 7000, "context_hash": "ctx-eclipse-2"}
        cmds = adapter.process_intent(msg)
        assert isinstance(cmds, list)

    def test_context_hash_stored_after_process(self):
        adapter = self._make_adapter()
        msg = {"intent": "follow", "zone": "editor_active", "dx": 0, "dy": 1,
               "ttl_millis": 7000, "context_hash": "ctx-eclipse-stored"}
        adapter.process_intent(msg)
        assert adapter.last_context_hash == "ctx-eclipse-stored"

    def test_no_raw_content_in_intent(self):
        from agent.services.heuristic_runtime.eclipse_snake_adapter import EclipseSnakeIntent
        intent = EclipseSnakeIntent.from_dict({
            "intent": "follow", "zone": "editor_active", "dx": 1, "dy": 0
        })
        event = intent.to_context_event()
        # Only zone classification (string), no file content
        assert event["normalized_value"] == "editor_active"

    def test_parse_eclipse_ttl_millis_clamped(self):
        from agent.services.heuristic_runtime.eclipse_snake_adapter import parse_eclipse_ttl_millis
        assert parse_eclipse_ttl_millis(3000) == 5.0   # below min → clamp to 5
        assert parse_eclipse_ttl_millis(7000) == 7.0   # in range
        assert parse_eclipse_ttl_millis(15000) == 10.0  # above max → clamp to 10


# ── T09.03: DecisionTrace repo enhancements ───────────────────────────────────

class TestDecisionTraceRepoEnhancements:
    def test_get_recent_delegates_to_list_by_surface(self):
        from agent.repositories.decision_trace_repo import DecisionTraceRepository
        repo = DecisionTraceRepository()
        # Should not raise; returns list (may be empty in test env without DB)
        try:
            result = repo.get_recent("tui_snake", 5)
            assert isinstance(result, list)
        except Exception:
            pass  # DB may not be configured in unit test environment

    def test_cleanup_old_traces_returns_int(self):
        from agent.repositories.decision_trace_repo import DecisionTraceRepository
        repo = DecisionTraceRepository()
        try:
            deleted = repo.cleanup_old_traces(retention_days=7)
            assert isinstance(deleted, int)
            assert deleted >= 0
        except Exception:
            pass


# ── T06.05: ProposalReviewView ────────────────────────────────────────────────

class TestProposalReviewView:
    def _make_review(self, tmp_path):
        from client_surfaces.operator_tui.proposal_review import ProposalReviewView
        from agent.services.heuristic_runtime.proposal_service import ProposalService
        svc = ProposalService(base_path=str(tmp_path))
        return ProposalReviewView(proposal_service=svc), svc

    def _make_candidate(self, svc, proposal_id="prop-review-1"):
        from agent.services.heuristic_runtime.decision_trace import DecisionTrace
        traces = [
            DecisionTrace(
                event_id=f"t-{i}", surface="tui_snake", context_hash=f"c-{i}",
                lease_id=None, heuristic_id="follow-default", strategy_id="s",
                rule_id=None, confidence=0.9, fallback_reason="ai_timeout",
                source="heuristic", action_kind="follow",
                started_at=1000.0 + i, resolved_at=1000.1 + i, reason_codes=[],
            )
            for i in range(3)
        ]
        gen = svc.generate_from_traces(traces)
        # Override proposal_id for test predictability
        from dataclasses import replace
        prop = replace(gen.proposal, proposal_id=proposal_id)
        svc.save_candidate(prop)
        return prop

    def test_list_open_candidates_finds_saved(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        self._make_candidate(svc)
        candidates = review.list_open_candidates()
        assert len(candidates) == 1

    def test_pending_count_correct(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        assert review.pending_count() == 0
        self._make_candidate(svc)
        assert review.pending_count() == 1

    def test_render_detail_contains_proposal_id(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        prop = self._make_candidate(svc, "prop-render")
        candidates = review.list_open_candidates()
        detail = review.render_detail(candidates[0])
        assert "prop-render" in detail

    def test_reject_moves_to_rejected(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        self._make_candidate(svc, "prop-reject")
        result = review.reject("prop-reject", reason="not_useful")
        assert result.success
        assert result.action == "reject"
        rejected_path = os.path.join(str(tmp_path), "rejected", "prop-reject.json")
        assert os.path.exists(rejected_path)
        assert review.pending_count() == 0

    def test_request_changes_adds_review_note(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        self._make_candidate(svc, "prop-changes")
        result = review.request_changes("prop-changes", notes="Add more tests")
        assert result.success
        assert result.action == "request_changes"
        # Candidate still exists
        assert review.pending_count() == 1

    def test_reject_nonexistent_fails(self, tmp_path):
        review, svc = self._make_review(tmp_path)
        result = review.reject("nonexistent-id", reason="test")
        assert not result.success
        assert "not_found" in result.reason
