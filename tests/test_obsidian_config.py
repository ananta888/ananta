"""Tests for ObsidianVaultProfile config (OBS-001).

Covers: profile validation, field defaults, validators.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.obsidian_config import ObsidianVaultProfile, load_vault_profiles


# ── Basic construction ────────────────────────────────────────────────────────

def test_profile_minimal():
    p = ObsidianVaultProfile(path="/some/vault")
    assert p.path == "/some/vault"
    assert p.enabled is True
    assert p.read_only is True
    assert p.privacy_filter_mode == "or"
    assert p.heading_chunk_level == 2


def test_profile_all_fields():
    p = ObsidianVaultProfile(
        path="/my/vault",
        name="personal",
        enabled=True,
        read_only=False,
        description="My personal vault",
        exclude_dirs=[".obsidian"],
        private_path_prefixes=["secret/"],
        privacy_filter_mode="and",
        heading_chunk_level=3,
        max_block_size_chars=1000,
        min_block_size_chars=20,
    )
    assert p.name == "personal"
    assert p.read_only is False
    assert p.privacy_filter_mode == "and"
    assert p.heading_chunk_level == 3


def test_profile_default_exclude_dirs():
    p = ObsidianVaultProfile(path="/v")
    assert ".obsidian" in p.exclude_dirs
    assert ".git" in p.exclude_dirs


# ── Validators ───────────────────────────────────────────────────────────────

def test_privacy_filter_mode_invalid():
    with pytest.raises(ValidationError) as exc_info:
        ObsidianVaultProfile(path="/v", privacy_filter_mode="invalid")
    assert "privacy_filter_mode" in str(exc_info.value)


def test_privacy_filter_mode_valid_values():
    for mode in ("or", "and", "off"):
        p = ObsidianVaultProfile(path="/v", privacy_filter_mode=mode)
        assert p.privacy_filter_mode == mode


def test_heading_chunk_level_out_of_range():
    with pytest.raises(ValidationError):
        ObsidianVaultProfile(path="/v", heading_chunk_level=0)
    with pytest.raises(ValidationError):
        ObsidianVaultProfile(path="/v", heading_chunk_level=7)


def test_heading_chunk_level_valid_range():
    for level in range(1, 7):
        p = ObsidianVaultProfile(path="/v", heading_chunk_level=level)
        assert p.heading_chunk_level == level


# ── load_vault_profiles ───────────────────────────────────────────────────────

def test_load_vault_profiles_empty():
    result = load_vault_profiles({})
    assert result == {}


def test_load_vault_profiles_single():
    raw = {"work": {"path": "/work/vault"}}
    profiles = load_vault_profiles(raw)
    assert "work" in profiles
    assert profiles["work"].name == "work"
    assert profiles["work"].path == "/work/vault"


def test_load_vault_profiles_multiple():
    raw = {
        "work": {"path": "/work/vault"},
        "personal": {"path": "/personal/vault", "privacy_filter_mode": "off"},
    }
    profiles = load_vault_profiles(raw)
    assert len(profiles) == 2
    assert profiles["personal"].privacy_filter_mode == "off"


def test_load_vault_profiles_name_injected():
    """name should be set from dict key if not in config."""
    raw = {"my-vault": {"path": "/v"}}
    profiles = load_vault_profiles(raw)
    assert profiles["my-vault"].name == "my-vault"


def test_load_vault_profiles_skips_non_dict():
    raw = {"good": {"path": "/v"}, "bad": "not-a-dict"}
    profiles = load_vault_profiles(raw)
    assert "good" in profiles
    assert "bad" not in profiles
