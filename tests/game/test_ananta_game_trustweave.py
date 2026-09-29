from __future__ import annotations

from agent.game.trustweave import TrustWeaveGraph


def test_trustweave_positive_event_increases_trust() -> None:
    graph = TrustWeaveGraph()
    result = graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="verified_artifact")
    assert result.delta > 0
    assert result.trust_value > 0


def test_trustweave_negative_event_decreases_trust() -> None:
    graph = TrustWeaveGraph()
    graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="verified_artifact")
    result = graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="policy_violation")
    assert result.delta < 0
    assert result.trust_value < 0.2


def test_trustweave_neutral_event_keeps_trust() -> None:
    graph = TrustWeaveGraph()
    graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="verified_artifact")
    before = graph.get_trust(source_id="agent:a", target_id="territory:x")
    result = graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="neutral_observation")
    assert result.delta == 0.0
    assert result.trust_value == before


def test_trustweave_exports_json_graph() -> None:
    graph = TrustWeaveGraph()
    graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="verified_artifact")
    payload = graph.to_json_graph()
    assert any(node["id"] == "agent:a" for node in payload["nodes"])
    assert any(edge["source"] == "agent:a" and edge["target"] == "territory:x" for edge in payload["edges"])


def test_trustweave_unknown_event_does_not_increase_trust() -> None:
    graph = TrustWeaveGraph()
    before = graph.get_trust(source_id="agent:a", target_id="territory:x")
    result = graph.apply_event(source_id="agent:a", target_id="territory:x", event_type="undefined_event")
    assert result.delta == 0.0
    assert result.trust_value == before
