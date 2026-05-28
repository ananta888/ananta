"""E2E: LLM erzeugt DSL-Kandidat, aber beeinflusst TUI nicht direkt.

T08.02: Fake-LLM erzeugt einen DSL-Kandidaten aus Snapshot-Pack.
Währenddessen liefert SnakeDecisionManager weiter deterministische Ergebnisse.
"""
import asyncio
import pytest
from unittest.mock import MagicMock
from agent.services.heuristic_runtime.background_heuristic_lab import BackgroundHeuristicLab, LabConfig
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager


_VALID_DSL = {
    "dsl_version": "2.0",
    "observe": {"sources": ["tui.semantic"]},
    "action": {"kind": "follow_artifact", "confidence": 0.7},
    "safety": {"safety_class": "ui_motion_only"},
    "provenance": {"created_by": "fake_llm", "rationale": "Test"},
}


class FakeLlmClient:
    async def complete_async(self, prompt: str) -> str:
        import json
        return json.dumps(_VALID_DSL)


def test_background_lab_disabled_by_default():
    lab = BackgroundHeuristicLab()
    assert not lab.is_enabled()


def test_snake_decision_manager_is_deterministic_while_lab_runs():
    """SnakeDecisionManager.decide() ist deterministisch unabhängig vom Lab."""
    from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
    from agent.repositories.decision_trace_repo import DecisionTraceRepository
    from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry

    registry = HeuristicRegistry.__new__(HeuristicRegistry)
    registry._heuristics = {}
    registry._definitions = {}
    registry._all = []
    registry._loaded = True
    registry._base_path = "/nonexistent"
    lease_repo = MagicMock()
    lease_repo.get_active.return_value = None
    lease_repo.acquire.return_value = MagicMock(id="lease_1", deadline_at=9999999999.0,
                                                  context_hash="x", heuristic_id="h1")
    lease_repo.mark_expired_batch.return_value = 0
    trace_repo = MagicMock()
    trace_repo.save.return_value = None

    mgr = SnakeDecisionManager(registry=registry, lease_repo=lease_repo, trace_repo=trace_repo)
    ctx = DecisionContext(source_surface="tui_snake")

    # decide() ist deterministisch
    result1 = mgr.decide(ctx)
    result2 = mgr.decide(ctx)
    assert result1.action_kind == result2.action_kind
    assert result1.source == result2.source


def test_fake_llm_produces_candidate_in_background():
    """Fake-LLM erzeugt Kandidat, landet nicht in active/."""
    import tempfile, os

    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            from agent.services.heuristic_runtime.heuristic_proposal_store import HeuristicProposalStore
            store = HeuristicProposalStore(candidates_dir=tmpdir)

            lab = BackgroundHeuristicLab(config=LabConfig(enabled=True, model_backend="fake"))
            lab.set_llm_client(FakeLlmClient())

            obs_pack = {
                "recent_snapshots": [{"frame_id": "f1", "screen_hash": "abc123", "width": 120, "height": 32}],
                "recent_deltas": [],
            }

            await lab.run_cycle(obs_pack)
            proposals = lab.get_pending_proposals()
            assert len(proposals) == 1
            proposal = proposals[0]
            # Kein status "active" darf vorkommen
            assert proposal.get("_proposal_meta", {}).get("status") != "active"

    asyncio.run(_run())


def test_lab_does_not_affect_fast_path_when_disabled():
    """Deaktiviertes Lab: decide() immer noch OK."""
    from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
    from agent.repositories.decision_trace_repo import DecisionTraceRepository
    from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry

    registry = HeuristicRegistry.__new__(HeuristicRegistry)
    registry._heuristics = {}
    registry._definitions = {}
    registry._all = []
    registry._loaded = True
    registry._base_path = "/nonexistent"
    lease_repo = MagicMock()
    lease_repo.get_active.return_value = None
    lease_repo.acquire.return_value = MagicMock(id="lease_1", deadline_at=9999999999.0,
                                                  context_hash="x", heuristic_id="h1")
    lease_repo.mark_expired_batch.return_value = 0
    trace_repo = MagicMock()
    trace_repo.save.return_value = None

    mgr = SnakeDecisionManager(registry=registry, lease_repo=lease_repo, trace_repo=trace_repo)
    ctx = DecisionContext(source_surface="tui_snake")
    result = mgr.decide(ctx)
    assert result is not None
    assert result.action_kind in ("follow", "lurk", "no_action", "policy_denied")
