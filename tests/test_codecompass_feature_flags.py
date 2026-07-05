"""DD-015 + CRG-001/RIG-001: feature flags default off, opt-in via env."""
from __future__ import annotations

import os

import pytest

from agent.feature_flags import all_flags, is_enabled
from agent.feature_flags import (
    codecompass_crg,
    codecompass_rig,
    codecompass_spade,
    codecompass_sqlite,
)


def test_defaults_are_all_off():
    flags = all_flags()
    assert flags == {
        "crg": {
            "adapter_enabled": False,
            "strict_pinning": True,
            "allow_direct_sqlite_read": False,
        },
        "rig": {
            "adapter_enabled": False,
            "allow_manual_fixtures": True,
            "strict_coverage_gating": True,
        },
        "spade": {
            "cmake_extractor_enabled": False,
            "ctest_runner_enabled": False,
        },
        "sqlite": {
            "graph_store_enabled": False,
            "rig_tables_enabled": False,
        },
    }


def test_is_enabled_uses_qualified_name():
    assert is_enabled("crg.adapter_enabled") is False
    assert is_enabled("rig.allow_manual_fixtures") is True


def test_is_enabled_unknown_group_is_false():
    assert is_enabled("unknown_group.anything") is False


@pytest.mark.parametrize(
    "env_key,env_value,expected",
    [
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "1", True),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "true", True),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "yes", True),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "on", True),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "0", False),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "false", False),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "off", False),
        ("CODECOMPASS_CRG_ADAPTER_ENABLED", "", False),
    ],
)
def test_env_overrides_default(monkeypatch, env_key, env_value, expected):
    monkeypatch.setenv(env_key, env_value)
    flags = all_flags()
    assert flags["crg"]["adapter_enabled"] is expected


def test_strict_pinning_cannot_be_disabled_by_env_off(monkeypatch):
    """strict_pinning is a safety property, not a feature gate.

    Env can *enable* but not silently disable strict_pinning. This protects
    CRG-001 from accidentally accepting un-pinned upstream SQLite exports.
    """
    monkeypatch.setenv("CODECOMPASS_CRG_STRICT_PINNING", "off")
    assert is_enabled("crg.strict_pinning") is True


def test_manual_fixtures_are_escape_valve(monkeypatch):
    """rig.allow_manual_fixtures stays true regardless of rig.adapter_enabled.

    Manual fixtures (RIG-012) are the escape-valve (DD-012) for build-systems
    that have no upstream extractor yet.
    """
    monkeypatch.setenv("CODECOMPASS_RIG_ADAPTER_ENABLED", "0")
    assert is_enabled("rig.allow_manual_fixtures") is True