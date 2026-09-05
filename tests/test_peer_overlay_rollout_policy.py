import pytest

from agent.services.peer_overlay_rollout_policy import PeerOverlayRolloutPolicy


def test_all_rollout_features_default_off_and_remain_independent() -> None:
    policy = PeerOverlayRolloutPolicy()
    assert all(not row["effective"] for row in policy.matrix().values())
    enabled = PeerOverlayRolloutPolicy(
        enabled={"mesh": True, "data_overlay": True},
        gate_bindings={"mesh": True, "data_overlay": False},
    )
    assert enabled.matrix()["mesh"]["effective"] is True
    assert enabled.matrix()["data_overlay"]["effective"] is False
    assert enabled.matrix()["mls"]["effective"] is False


def test_canary_allowlists_are_independent_and_unavailable_features_remain_no_go() -> None:
    policy = PeerOverlayRolloutPolicy(
        enabled={"data_overlay": True, "media_overlay": True},
        gate_bindings={"data_overlay": True, "media_overlay": True},
        allowlists={
            "tenant": ["tenant-1"],
            "room": ["room-1"],
            "publication": ["publication-1"],
            "browser": ["browser-1"],
        },
    )
    allowed = policy.evaluate(
        "data_overlay",
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        browser_id="browser-1",
    )
    assert allowed.allowed is True
    denied = policy.evaluate(
        "data_overlay",
        tenant_id="tenant-1",
        room_id="room-2",
        publication_id="publication-1",
        browser_id="browser-1",
    )
    assert denied.reason_code == "peer_overlay_room_canary_denied"
    media = policy.evaluate(
        "media_overlay",
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        browser_id="browser-1",
    )
    assert media.reason_code == "peer_overlay_cross_peer_media_standard_no_go"
    unavailable = PeerOverlayRolloutPolicy(
        enabled={"native_sframe": True, "mls": True},
        gate_bindings={"native_sframe": True, "mls": True},
    )
    assert unavailable.matrix()["native_sframe"]["effective"] is False
    assert unavailable.matrix()["mls"]["effective"] is False
    assert (
        unavailable.evaluate(
            "native_sframe", tenant_id="tenant-1", room_id="room-1", publication_id="publication-1"
        ).reason_code
        == "peer_overlay_native_sframe_unavailable"
    )
    assert (
        unavailable.evaluate("mls", tenant_id="tenant-1", room_id="room-1", publication_id="publication-1").reason_code
        == "peer_overlay_mls_rejected"
    )


def test_unknown_rollout_fields_and_features_fail_closed() -> None:
    with pytest.raises(ValueError, match="fields_invalid"):
        PeerOverlayRolloutPolicy(enabled={"gossip": True})
    with pytest.raises(ValueError, match="feature_invalid"):
        PeerOverlayRolloutPolicy().evaluate(
            "gossip", tenant_id="tenant-1", room_id="room-1", publication_id="publication-1"
        )
    for invalid in (
        {"enabled": {"mesh": "true"}},
        {"allowlists": {"tenant": "tenant-1"}},
        {"enabled": True},
        {"unknown": {}},
    ):
        with pytest.raises(ValueError, match="fields_invalid"):
            PeerOverlayRolloutPolicy.from_mapping(invalid)
