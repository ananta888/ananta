from __future__ import annotations

from pathlib import Path

import yaml

from scripts.build_semantic_media_containers import (
    BUILDS,
    SFU_DIGEST,
    SFU_REFERENCE,
    SFU_TAG,
    TURN_DIGEST,
    TURN_REFERENCE,
    TURN_TAG,
)
from scripts.generate_semantic_media_supply_chain_reports import COMPONENT_SOURCES

ROOT = Path(__file__).resolve().parents[1]


def test_container_build_inventory_covers_every_supply_chain_component() -> None:
    assert {build.component for build in BUILDS} == {
        "hub",
        "frontend",
        "reconciliation",
        "training",
    }
    assert len({build.tag for build in BUILDS}) == len(BUILDS)
    assert all(build.command[:2] == ("docker", "build") for build in BUILDS)
    assert SFU_REFERENCE == f"{SFU_TAG}@sha256:{SFU_DIGEST}"
    assert len(SFU_DIGEST) == 64
    assert TURN_REFERENCE == f"{TURN_TAG}@sha256:{TURN_DIGEST}"
    assert len(TURN_DIGEST) == 64
    assert {build.component for build in BUILDS} | {"sfu", "turn"} == set(COMPONENT_SOURCES)
    assert COMPONENT_SOURCES["sfu"] == SFU_REFERENCE
    assert COMPONENT_SOURCES["turn"] == TURN_REFERENCE


def test_turn_gate_retains_only_the_capability_required_by_upstream_binary() -> None:
    document = yaml.safe_load((ROOT / "docker-compose.semantic-media.yml").read_text(encoding="utf-8"))
    turn = document["services"]["semantic-media-turn-gate"]
    sfu = document["services"]["semantic-media-sfu"]

    assert turn["cap_drop"] == ["ALL"]
    assert turn["cap_add"] == ["NET_BIND_SERVICE"]
    assert sfu["cap_drop"] == ["ALL"]
    assert not sfu.get("cap_add")
    assert turn["read_only"] is True
    assert turn["user"] == "65534:65534"
    assert turn["ports"] == ["127.0.0.1:${ANANTA_SEMANTIC_MEDIA_TURN_GATE_PORT:-3479}:3478/udp"]
