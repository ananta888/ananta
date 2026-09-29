from __future__ import annotations

from scripts.e2e.sfu_broadcast_local_multinode_e2e import (
    LIVEKIT_IMAGE,
    REDIS_IMAGE,
)
from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import TURN_REPO_DIGEST
from scripts.run_hub_evidence_sfu_multinode_gate import projection_passed


def _projection() -> dict:
    engine_rows = [
        {
            "engine": engine,
            "verdict": "pass",
            "publisher_outbound_video_bytes": 100,
            "receiver_inbound_video_bytes": [80, 70],
            "receiver_decoded_samples": [10, 9],
            "wrong_key_inbound_video_bytes": 60,
            "wrong_key_decoded_samples": 0,
        }
        for engine in ("chromium", "firefox")
    ]
    return {
        "status": "passed",
        "scope": "local_single_host",
        "claims": {
            "real_livekit_processes": True,
            "real_tls_redis_process": True,
            "real_browser_processes": True,
            "native_placement_owner": "livekit",
            "public_network_path": False,
            "independent_failure_domains": False,
            "production_capacity": False,
        },
        "pinned_images": {
            "livekit": LIVEKIT_IMAGE,
            "redis": REDIS_IMAGE,
            "coturn": TURN_REPO_DIGEST,
        },
        "container_image_ids": {
            name: f"sha256:{index:064x}"
            for index, name in enumerate(
                (
                    "sfu-broadcast-redis",
                    "sfu-broadcast-livekit-native-a",
                    "sfu-broadcast-livekit-native-b",
                    "coturn",
                ),
                start=1,
            )
        },
        "topology": {"livekit_nodes": 2, "redis_nodes": 1, "host_count": 1},
        "observations": {
            "initial_registered_nodes": 2,
            "drained_registered_nodes": 1,
            "rejoined_registered_nodes": 2,
            "drain_recovery_ms": 9000,
            "after_drain_media": {"verdict": "pass", "engines": engine_rows},
        },
        "cleanup": {
            "owned_containers_and_volumes_removed": True,
            "owned_turn_container_removed": True,
        },
    }


def test_projection_accepts_real_bounded_multinode_observations() -> None:
    assert projection_passed(_projection()) is True


def test_projection_rejects_public_or_failed_cleanup_claims() -> None:
    projection = _projection()
    projection["claims"]["public_network_path"] = True
    projection["cleanup"]["owned_turn_container_removed"] = False

    assert projection_passed(projection) is False
