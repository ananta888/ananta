"""Contract tests for X86CC-002: feature-flags + profile validation.

Asserts:
- default off (ANANTA_CODECOMPASS_X86_ENABLED unset -> False)
- explicit on works
- invalid profile raises diagnostic and falls back to unknown_x86 (NEVER wrong default)
- all five profiles load cleanly
- non-positive integer limit is rejected with diagnostic
"""

from __future__ import annotations

from agent.codecompass.x86.config import (
    VALID_PROFILES,
    X86Config,
    load_x86_config,
)


def test_default_off_when_no_env():
    cfg = load_x86_config(env={})
    assert cfg.enabled is False, "master switch must default off"


def test_explicit_on_via_env():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_ENABLED": "1"})
    assert cfg.enabled is True


def test_all_known_profiles_load():
    for profile in VALID_PROFILES:
        cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_DEFAULT_PROFILE": profile})
        assert cfg.default_profile == profile, f"profile {profile!r} did not stick"
        assert "unsupported_x86_profile" not in cfg.diagnostics


def test_invalid_profile_does_not_silently_fall_back():
    """A wrong profile must produce a diagnostic and never silently masquerade as a valid one."""
    cfg = load_x86_config(
        env={"ANANTA_CODECOMPASS_X86_DEFAULT_PROFILE": "x86_64_obviously_bogus"}
    )
    assert cfg.default_profile == "unknown_x86"
    assert any("unsupported_x86_profile" in d for d in cfg.diagnostics)


def test_subflags_default_true_when_master_on():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_ENABLED": "true"})
    assert cfg.raw_assembly_indexing is True
    assert cfg.binary_metadata_indexing is True
    assert cfg.disassembler_export_indexing is True
    assert cfg.cfg_indexing is True
    assert cfg.experimental_adapter is False


def test_subflags_can_be_disabled_individually():
    cfg = load_x86_config(
        env={
            "ANANTA_CODECOMPASS_X86_ENABLED": "true",
            "ANANTA_CODECOMPASS_X86_RAW_ASSEMBLY": "false",
            "ANANTA_CODECOMPASS_X86_CFG": "off",
        }
    )
    assert cfg.raw_assembly_indexing is False
    assert cfg.cfg_indexing is False
    assert cfg.binary_metadata_indexing is True  # untouched


def test_experimental_adapter_default_off():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_ENABLED": "true"})
    assert cfg.experimental_adapter is False


def test_non_positive_integer_limit_rejected():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_MAX_INSTRUCTIONS": "0"})
    assert cfg.max_instructions == 50_000  # default preserved
    assert "non_positive_integer_config" in cfg.diagnostics


def test_negative_integer_limit_rejected():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_MAX_FUNCTIONS": "-1"})
    assert cfg.max_functions == 5_000
    assert "non_positive_integer_config" in cfg.diagnostics


def test_invalid_integer_limit_does_not_raise():
    cfg = load_x86_config(env={"ANANTA_CODECOMPASS_X86_MAX_STRINGS": "not_a_number"})
    assert cfg.max_strings == 10_000
    assert "invalid_integer_config" in cfg.diagnostics


def test_config_is_frozen():
    cfg = load_x86_config(env={})
    try:
        cfg.enabled = True  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("X86Config must be frozen so env-loading is deterministic")


def test_valid_profiles_set_is_stable():
    """If anyone shrinks VALID_PROFILES, downstream code paths break loudly. Pin them."""
    expected = {"x86_64_sysv", "x86_64_windows", "x86_32_cdecl", "x86_32_stdcall", "unknown_x86"}
    assert VALID_PROFILES == expected, f"VALID_PROFILES drifted: {VALID_PROFILES}"