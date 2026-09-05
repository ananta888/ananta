from __future__ import annotations

from pathlib import Path

import yaml

from scripts.e2e.sfu_broadcast_local_multinode_e2e import media_passed, project_media


def _media_result() -> dict:
    peers = [
        {"identity": "publisher", "outbound_video_bytes": 100},
        {"identity": "receiver-1", "inbound_video_bytes": 90, "decoded_samples": 10},
        {"identity": "receiver-2", "inbound_video_bytes": 80, "decoded_samples": 9},
        {"identity": "wrong-key-probe", "inbound_video_bytes": 70, "decoded_samples": 0},
    ]
    return {
        "verdict": "pass",
        "engines": [{"engine": engine, "verdict": "pass", "peers": peers} for engine in ("chromium", "firefox")],
    }


def test_projection_retains_only_bounded_media_metrics() -> None:
    projection = project_media(_media_result())

    assert media_passed(projection) is True
    assert projection["engines"][0]["receiver_decoded_samples"] == [10, 9]
    assert "peers" not in projection["engines"][0]


def test_media_verdict_is_recomputed_from_negative_probe() -> None:
    projection = project_media(_media_result())
    projection["engines"][0]["wrong_key_decoded_samples"] = 1

    assert media_passed(projection) is False


def test_committed_distributed_config_uses_verified_redis_tls() -> None:
    root = Path(__file__).resolve().parents[1]
    livekit = yaml.safe_load((root / "config/livekit.sfu-broadcast-native.yaml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((root / "docker-compose.sfu-broadcast.yml").read_text(encoding="utf-8"))

    assert livekit["redis"]["tls"] == {
        "enabled": True,
        "insecure": False,
        "server_name": "sfu-broadcast-redis",
        "ca_cert_file": "/run/sfu-redis-tls/ca.crt",
        "client_cert_file": "/run/sfu-redis-tls/client.crt",
        "client_key_file": "/run/sfu-redis-tls/client.key",
    }
    assert "@sha256:" in compose["services"]["sfu-broadcast-redis"]["image"]
