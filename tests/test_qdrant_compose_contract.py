from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker/compose-next/compose.qdrant.yml"
EXPECTED_IMAGE = (
    "qdrant/qdrant:v1.18.2@"
    "sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c"
)


def test_qdrant_compose_profile_is_pinned_private_and_persistent() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = payload["services"]["qdrant"]

    assert service["profiles"] == ["qdrant"]
    assert service["image"] == EXPECTED_IMAGE
    assert service["restart"] == "unless-stopped"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["pids_limit"] == 256
    assert service["healthcheck"]["test"][0] == "CMD-SHELL"
    rendered_entrypoint = "\n".join(service["entrypoint"])
    assert "$(cat /run/secrets/qdrant-api-key)" in rendered_entrypoint
    assert "$$(cat /run/secrets/qdrant-api-key)" not in rendered_entrypoint
    assert "qdrant-data:/qdrant/storage" in service["volumes"]
    assert "qdrant-snapshots:/qdrant/snapshots" in service["volumes"]
    assert payload["networks"]["qdrant-worker"]["internal"] is True
    assert set(service["networks"]) == {"qdrant-worker", "qdrant-edge"}
    assert "qdrant-edge" in payload["networks"]
    assert all(
        "qdrant-edge" not in (other.get("networks") or ())
        for name, other in payload["services"].items()
        if name != "qdrant"
    )


def test_qdrant_compose_ports_default_to_loopback_and_secret_is_not_environment() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    service = payload["services"]["qdrant"]

    assert all("127.0.0.1" in item for item in service["ports"])
    assert "QDRANT__SERVICE__API_KEY:" not in text
    assert "/run/secrets/qdrant-api-key" in service["entrypoint"][2]
    assert payload["secrets"]["qdrant-api-key"]["file"].startswith(
        "${ANANTA_QDRANT_API_KEY_FILE"
    )
    assert set(payload["services"]["ai-agent-alpha"]["networks"]) == {
        "default",
        "qdrant-worker",
    }
    assert "qdrant-worker" not in payload["services"].get("ai-agent-hub", {}).get(
        "networks",
        [],
    )
