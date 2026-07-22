from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_turn_compose_is_profile_gated_and_hardened():
    document = yaml.safe_load((ROOT / "docker-compose.sfu-broadcast-turn.yml").read_text())

    assert set(document["services"]) == {
        "turn-a",
        "turn-b",
        "turn-observer-a",
        "turn-observer-b",
    }
    for service in document["services"].values():
        assert service["profiles"] == ["sfu_broadcast_turn"]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["user"] == "65532:65532"

    assert document["services"]["turn-observer-a"]["network_mode"] == "service:turn-a"
    assert document["services"]["turn-observer-b"]["network_mode"] == "service:turn-b"
    assert "9641" not in repr(document["services"]["turn-a"].get("ports", []))
    assert "9641" not in repr(document["services"]["turn-b"].get("ports", []))


def test_coturn_contract_has_no_open_relay_or_username_metrics():
    config = (ROOT / "config/coturn/sfu-broadcast-turn.conf").read_text()

    assert "use-auth-secret" in config
    assert "no-multicast-peers" in config
    assert "denied-peer-ip=127.0.0.0-127.255.255.255" in config
    assert "prometheus-address=127.0.0.1" in config
    assert "prometheus-username-labels" not in "\n".join(
        line for line in config.splitlines() if not line.lstrip().startswith("#")
    )
    assert "no-auth" not in config

