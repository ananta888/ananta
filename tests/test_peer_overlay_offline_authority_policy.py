from __future__ import annotations

import pytest

from agent.services.peer_overlay_offline_authority_policy import PeerOverlayOfflineAuthorityPolicy


def test_profiles_bound_offline_grace_and_caller_cannot_extend_them() -> None:
    policy = PeerOverlayOfflineAuthorityPolicy()
    assert policy.resolve("strict").maximum_grace_seconds == 30
    assert policy.resolve("balanced").maximum_grace_seconds == 60
    assert policy.resolve("availability", 999).maximum_grace_seconds == 120
    assert policy.resolve("availability", 15).maximum_grace_seconds == 15


def test_unknown_profile_and_invalid_grace_fail_closed() -> None:
    policy = PeerOverlayOfflineAuthorityPolicy()
    with pytest.raises(ValueError, match="offline_profile_invalid"):
        policy.resolve("custom")
    with pytest.raises(ValueError, match="offline_grace_invalid"):
        policy.resolve("strict", -1)
