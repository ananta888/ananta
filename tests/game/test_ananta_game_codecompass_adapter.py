from __future__ import annotations

from agent.game.codecompass_adapter import build_game_map_from_repository
from agent.game.models import GameMap


def test_adapter_maps_paths_to_territories_with_explicit_risk() -> None:
    game_map = build_game_map_from_repository(
        repo_paths=["agent/services", "docs/ananta-game"],
        risk_overrides={"agent/services": "high"},
        context_overrides={
            "agent/services": {"visibility": "allow", "allowed_roles": ["planner"], "local_only": True}
        },
    )

    assert game_map.degraded is False
    assert [territory.path for territory in game_map.territories] == ["agent/services", "docs/ananta-game"]
    assert game_map.territories[0].risk_level == "high"
    assert game_map.context_gates[0].visibility == "allow"
    assert game_map.context_gates[0].allowed_roles == ("planner",)
    assert game_map.context_gates[0].local_only is True


def test_adapter_maps_dependency_edges_to_trust_edges() -> None:
    game_map = build_game_map_from_repository(
        repo_paths=["agent/services", "agent/routes"],
        dependency_edges=[("agent/routes", "agent/services")],
    )

    assert len(game_map.trust_edges) == 1
    edge = game_map.trust_edges[0]
    assert edge.relationship == "dependency"
    assert edge.source_id == "territory:agent/routes"
    assert edge.target_id == "territory:agent/services"


def test_adapter_returns_degraded_valid_map_without_repo_paths() -> None:
    game_map = build_game_map_from_repository(repo_paths=[])

    assert isinstance(game_map, GameMap)
    assert game_map.degraded is True
    assert game_map.metadata["reason"] == "missing_repo_paths"
    assert game_map.territories == ()
    assert "content" not in game_map.to_json()
