"""Tests for the Predictive UI Guide (PUG) configuration layer.

Covers the 7 new session-scoped settings introduced in Welle 1 and the
3 preset functions (quiet / balanced / eager).
"""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_state import (
    PREDICTIVE_GUIDE_KEYS,
    PREDICTIVE_PRESETS,
    default_sessions,
    get_sessions,
    make_session,
    update_session_settings,
)


# ── Defaults ─────────────────────────────────────────────────────────────────


def test_predictive_guide_keys_constant_lists_all_seven():
    """All 7 predictive-guide setting keys must be exported in one constant."""
    expected = {
        "predictive_guide_enabled",
        "predictive_guide_mode",
        "predictive_guide_dwell_ms",
        "predictive_guide_min_confidence",
        "predictive_guide_ttl_seconds",
        "predictive_guide_multi_candidates",
        "predictive_guide_log_deltas_only",
    }
    assert set(PREDICTIVE_GUIDE_KEYS) == expected


def test_default_session_settings_includes_predictive_guide_keys():
    """_DEFAULT_SESSION_SETTINGS must define a default for every PUG key."""
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS
    for k in PREDICTIVE_GUIDE_KEYS:
        assert k in _DEFAULT_SESSION_SETTINGS, f"missing default for {k}"


def test_predictive_guide_default_is_disabled():
    """Master toggle defaults to False — the user must opt in."""
    sess = make_session(session_id="custom", name="Custom")
    assert sess["settings"]["predictive_guide_enabled"] is False


def test_predictive_guide_default_mode_is_balanced():
    """Default mode is 'balanced' when the master toggle is off."""
    sess = make_session(session_id="custom", name="Custom")
    assert sess["settings"]["predictive_guide_mode"] == "balanced"


# ── Presets ─────────────────────────────────────────────────────────────────


def test_three_presets_exist():
    assert set(PREDICTIVE_PRESETS.keys()) == {"quiet", "balanced", "eager"}


def test_quiet_preset_is_high_dwell_high_confidence():
    p = PREDICTIVE_PRESETS["quiet"]
    assert p["predictive_guide_dwell_ms"] >= 2000
    assert p["predictive_guide_min_confidence"] >= 0.65
    assert p["predictive_guide_multi_candidates"] == 1


def test_eager_preset_is_low_dwell_low_confidence_multi_candidate():
    p = PREDICTIVE_PRESETS["eager"]
    assert p["predictive_guide_dwell_ms"] <= 800
    assert p["predictive_guide_min_confidence"] <= 0.45
    assert p["predictive_guide_multi_candidates"] >= 3


def test_balanced_preset_sits_between_quiet_and_eager():
    q = PREDICTIVE_PRESETS["quiet"]
    b = PREDICTIVE_PRESETS["balanced"]
    e = PREDICTIVE_PRESETS["eager"]
    assert q["predictive_guide_dwell_ms"] >= b["predictive_guide_dwell_ms"] >= e["predictive_guide_dwell_ms"]
    assert q["predictive_guide_min_confidence"] >= b["predictive_guide_min_confidence"] >= e["predictive_guide_min_confidence"]


def test_all_presets_set_every_predictive_key():
    """Each preset must fully populate all 7 PUG keys — no partial presets."""
    for name, preset in PREDICTIVE_PRESETS.items():
        for k in PREDICTIVE_GUIDE_KEYS:
            assert k in preset, f"preset {name!r} missing key {k!r}"


# ── Round-trip through update_session_settings ──────────────────────────────


def test_chat_session_predictive_settings_round_trip():
    """PATCH /api/chat/sessions/<id> must persist predictive-guide settings."""
    chat: dict = {}
    sessions = get_sessions(chat)
    sid = sessions[0]["id"]

    ok = update_session_settings(chat, sid, {
        "predictive_guide_enabled": True,
        "predictive_guide_dwell_ms": 2500,
        "predictive_guide_multi_candidates": 4,
    })
    assert ok is True
    saved = next(s for s in get_sessions(chat) if s["id"] == sid)
    assert saved["settings"]["predictive_guide_enabled"] is True
    assert saved["settings"]["predictive_guide_dwell_ms"] == 2500
    assert saved["settings"]["predictive_guide_multi_candidates"] == 4
    # Untouched keys keep their defaults
    assert saved["settings"]["predictive_guide_mode"] == "balanced"


def test_none_value_resets_predictive_setting_to_default():
    """Sending None for a PUG key must reset it to the _DEFAULT value."""
    chat: dict = {}
    sessions = get_sessions(chat)
    sid = sessions[0]["id"]
    update_session_settings(chat, sid, {
        "predictive_guide_dwell_ms": 4000,
        "predictive_guide_min_confidence": 0.9,
    })
    update_session_settings(chat, sid, {
        "predictive_guide_dwell_ms": None,
    })
    saved = next(s for s in get_sessions(chat) if s["id"] == sid)
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS
    assert saved["settings"]["predictive_guide_dwell_ms"] == _DEFAULT_SESSION_SETTINGS["predictive_guide_dwell_ms"]
    # The non-reset key stays
    assert saved["settings"]["predictive_guide_min_confidence"] == 0.9


# ── Built-in sessions expose the new keys too ───────────────────────────────


def test_ananta_visual_session_has_predictive_keys():
    """All built-in sessions (including ananta-visual) get the new defaults."""
    for s in default_sessions():
        for k in PREDICTIVE_GUIDE_KEYS:
            assert k in s["settings"], f"session {s['id']!r} missing {k!r}"
