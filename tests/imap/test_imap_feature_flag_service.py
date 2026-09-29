from __future__ import annotations

from agent.services.imap_feature_flag_service import resolve_imap_feature_flags, resolve_imap_runtime_state


def test_imap_feature_flags_default_to_disabled() -> None:
    flags = resolve_imap_feature_flags({})
    assert flags["enabled"] is False
    assert flags["sync_policy"] == "manual"


def test_imap_runtime_state_is_disabled_without_account() -> None:
    state = resolve_imap_runtime_state({"imap": {"enabled": True}}, has_account=False, connected=False, syncing=False)
    assert state["state"] == "disabled"


def test_imap_runtime_states_cover_offline_connected_and_syncing() -> None:
    cfg = {"imap": {"enabled": True, "sync_enabled": True}}
    offline = resolve_imap_runtime_state(cfg, has_account=True, connected=False, syncing=False)
    connected = resolve_imap_runtime_state(cfg, has_account=True, connected=True, syncing=False)
    syncing = resolve_imap_runtime_state(cfg, has_account=True, connected=True, syncing=True)
    assert offline["state"] == "offline"
    assert connected["state"] == "connected"
    assert syncing["state"] == "syncing"
