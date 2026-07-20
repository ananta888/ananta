from __future__ import annotations

from agent.services.share_session_relay_membership import ShareSessionRelayMembership


class Sessions:
    def get_session(self, session_id: str):
        if session_id != "session":
            return None
        return {
            "id": "session",
            "tenant_id": "tenant",
            "owner_user_id": "owner",
            "permissions": {"chat": True, "view_tui": True, "artifact_share": False},
            "revoked_at": None,
        }

    def get_participants(self, session_id: str):
        assert session_id == "session"
        return [
            {
                "user_id": "alice",
                "permissions": {"chat": True, "view_tui": True, "artifact_share": False},
                "revoked_at": None,
            },
            {
                "user_id": "revoked",
                "permissions": {"chat": True},
                "revoked_at": 1,
            },
        ]


def test_membership_is_bilateral_epoch_bound_and_least_privilege() -> None:
    adapter = ShareSessionRelayMembership(Sessions(), epoch_resolver=lambda _session: 7, clock=lambda: 1)
    owner = adapter.member(tenant_id="tenant", session_id="session", member_id="owner")
    alice = adapter.member(tenant_id="tenant", session_id="session", member_id="alice")
    assert owner is not None and alice is not None
    assert owner.epoch == alice.epoch == 7
    assert owner.send_audiences == frozenset({"alice"})
    assert "semantic_control" in owner.permissions
    assert "semantic_visual_receive" in alice.permissions
    assert "peer_evidence_sync" not in alice.permissions
    assert adapter.member(tenant_id="tenant", session_id="session", member_id="revoked") is None
    assert adapter.member(tenant_id="other", session_id="session", member_id="owner") is None
