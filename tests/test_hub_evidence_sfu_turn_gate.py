from __future__ import annotations

from scripts.run_hub_evidence_sfu_turn_gate import (
    LIVEKIT_REPO_DIGEST,
    TURN_REPO_DIGEST,
    project_relay_result,
    projection_passed,
    repository_image_digest,
)


def _result() -> dict:
    peers = [
        {"identity": "publisher", "outbound_video_bytes": 100},
        {"identity": "receiver-1", "inbound_video_bytes": 90, "decoded_samples": 10},
        {"identity": "receiver-2", "inbound_video_bytes": 80, "decoded_samples": 9},
        {"identity": "receiver-3", "inbound_video_bytes": 70, "decoded_samples": 8},
        {"identity": "wrong-key-probe", "inbound_video_bytes": 60, "decoded_samples": 0},
    ]
    return {
        "status": "passed",
        "claims": {
            "real_browser_contexts": True,
            "real_livekit_process": True,
            "real_coturn_relay_selected": True,
            "wrong_key_media_not_decoded": True,
            "production_capacity": False,
        },
        "pinned_images": {"livekit": LIVEKIT_REPO_DIGEST, "coturn": TURN_REPO_DIGEST},
        "container_image_ids": {
            "livekit": f"sha256:{repository_image_digest(LIVEKIT_REPO_DIGEST)}",
            "coturn": f"sha256:{repository_image_digest(TURN_REPO_DIGEST)}",
        },
        "cleanup": {"compose_project_removed": True, "host_turn_container_removed": True},
        "source_report": {
            "transport_profile": "turn_relay_required",
            "topology": {"publishers": 1, "receivers": 3},
            "e2ee": {"enabled": True, "server_plaintext_access": False},
            "engines": [
                {"engine": engine, "relay_required": True, "relay_selected": True, "peers": peers}
                for engine in ("chromium", "firefox")
            ],
        },
    }


def test_projection_retains_bounded_media_and_negative_path_metrics() -> None:
    projection = project_relay_result(_result())

    assert projection_passed(projection, receiver_count=3) is True
    assert projection["engines"][0]["receiver_decoded_samples"] == [10, 9, 8]
    assert projection["engines"][0]["wrong_key_decoded_samples"] == 0
    assert "peers" not in projection["engines"][0]


def test_projection_rejects_direct_fallback_or_wrong_key_decode() -> None:
    projection = project_relay_result(_result())
    projection["engines"][0]["relay_selected"] = False
    projection["engines"][1]["wrong_key_decoded_samples"] = 1

    assert projection_passed(projection, receiver_count=3) is False
