import pytest

from agent.services.peer_overlay_release_gate import PeerOverlayReleaseGate


def test_data_overlay_never_promotes_local_results_without_authoritative_evidence() -> None:
    result = PeerOverlayReleaseGate().evaluate(
        path="data_overlay",
        requested_stage="data_canary",
        local_gates={key: True for key in ("contracts", "security", "churn", "backpressure", "fallback")},
        source_refs=[],
        run_refs=[],
    )
    assert result["release_allowed"] is False
    assert result["source_refs"] == []
    assert result["run_refs"] == []
    assert result["human_intervention_required"] is False


def test_media_overlay_remains_no_go_without_assignment_bound_evidence() -> None:
    result = PeerOverlayReleaseGate().evaluate(
        path="media_overlay",
        requested_stage="media_internal",
        local_gates={
            key: True for key in ("contracts", "standards", "browser", "security", "nat", "quality", "fallback")
        },
        source_refs=[],
        run_refs=[],
    )
    assert result["release_allowed"] is False
    assert "peer_overlay_cross_peer_media_standard_no_go" in result["reason_codes"]
    assert result["fallback"] == "livekit_e2ee"


def test_release_gate_rejects_unknown_paths_and_stages() -> None:
    gate = PeerOverlayReleaseGate()
    with pytest.raises(ValueError, match="release_path_invalid"):
        gate.evaluate(path="gossip", requested_stage="general", local_gates={}, source_refs=[], run_refs=[])
    with pytest.raises(ValueError):
        gate.evaluate(path="mesh", requested_stage="unknown", local_gates={}, source_refs=[], run_refs=[])
