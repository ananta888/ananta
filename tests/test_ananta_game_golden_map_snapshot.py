from __future__ import annotations

import json
from pathlib import Path

from agent.game.models import (
    AgentUnit,
    ArtifactObjective,
    CodeTerritory,
    ContextGate,
    GameMap,
    PolicyNode,
    TrustEdge,
)


def _golden_path() -> Path:
    return Path(__file__).parent / "golden" / "ananta-game" / "demo-gamemap.json"


def _build_demo_game_map() -> GameMap:
    return GameMap(
        id="map:golden-demo",
        title="Ananta Strategy Golden Map",
        territories=(
            CodeTerritory(
                id="territory:agent/services",
                name="agent/services",
                path="agent/services",
                module="agent",
                risk_level="high",
            ),
            CodeTerritory(
                id="territory:data/secrets",
                name="data/secrets",
                path="data/secrets",
                module="data",
                risk_level="critical",
            ),
        ),
        agents=(
            AgentUnit(
                id="agent:hub",
                role="hub",
                capabilities=("plan", "delegate"),
                allowed_context=("repo:index",),
            ),
        ),
        policy_nodes=(
            PolicyNode(
                id="policy:secret-cloud",
                policy_type="context_access",
                effect="deny",
                scope=("cloud_worker", "secret_paths"),
                reason="secret_or_local_only",
            ),
        ),
        context_gates=(
            ContextGate(
                id="context:territory:agent/services",
                territory_id="territory:agent/services",
                visibility="allow",
                allowed_roles=("local_worker",),
                local_only=True,
            ),
            ContextGate(
                id="context:territory:data/secrets",
                territory_id="territory:data/secrets",
                visibility="redacted",
                local_only=True,
                secret=True,
            ),
        ),
        artifact_objectives=(
            ArtifactObjective(
                id="objective:1",
                task_id="ASG-011",
                artifact_kind="report",
                verification_required=True,
                evidence_refs=("artifact:1",),
                status="open",
            ),
        ),
        trust_edges=(
            TrustEdge(
                id="dep:agent/services->data/secrets",
                source_id="territory:agent/services",
                target_id="territory:data/secrets",
                relationship="dependency",
                trust_value=0.2,
            ),
        ),
        metadata={"source": "golden_fixture"},
    )


def test_golden_map_snapshot_matches_expected_json() -> None:
    expected = json.loads(_golden_path().read_text(encoding="utf-8"))
    payload = json.loads(_build_demo_game_map().to_json())
    assert payload == expected


def test_golden_map_is_roundtrip_serializable() -> None:
    expected = _golden_path().read_text(encoding="utf-8")
    loaded = GameMap.from_json(expected)
    assert json.loads(loaded.to_json()) == json.loads(expected)
