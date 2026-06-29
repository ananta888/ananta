"""Tests for ObsidianPrivacyFilter (OBS-003)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag-helper"))

from rag_helper.application.privacy_filter import (
    PrivacyResult,
    is_private,
    list_excluded,
)
from rag_helper.application.vault_scanner import scan

VAULT_DIR = Path(__file__).parent / "fixtures" / "obsidian_test_vault"


class _Profile:
    def __init__(self, **kwargs):
        self.private_path_prefixes = kwargs.get("private_path_prefixes", ["private/", "_private"])
        self.private_frontmatter_field = kwargs.get("private_frontmatter_field", "private")
        self.private_frontmatter_truthy_values = kwargs.get(
            "private_frontmatter_truthy_values", [True, "true", "yes", "1"]
        )
        self.private_tags = kwargs.get("private_tags", ["no-index", "private", "personal"])
        self.privacy_filter_mode = kwargs.get("privacy_filter_mode", "or")
        self.exclude_dirs = [".obsidian", ".git", ".trash", "assets"]
        self.exclude_glob_patterns = []
        self.index_canvas_files = True
        self.path = str(VAULT_DIR)


# ── Path prefix checks ────────────────────────────────────────────────────────

def test_private_path_prefix_excluded():
    profile = _Profile()
    result = is_private("private/secret.md", {}, [], profile)
    assert result.excluded is True
    assert result.matched_mechanism == "path_prefix"


def test_private_underscore_prefix_excluded():
    profile = _Profile()
    result = is_private("_private/notes.md", {}, [], profile)
    assert result.excluded is True
    assert result.matched_mechanism == "path_prefix"


def test_non_private_path_not_excluded():
    profile = _Profile()
    result = is_private("projects/Alpha.md", {}, [], profile)
    assert result.excluded is False


# ── Frontmatter checks ────────────────────────────────────────────────────────

def test_frontmatter_private_true_excluded():
    profile = _Profile()
    result = is_private("some/note.md", {"private": True}, [], profile)
    assert result.excluded is True
    assert result.matched_mechanism == "frontmatter"


def test_frontmatter_private_string_true_excluded():
    profile = _Profile()
    result = is_private("note.md", {"private": "true"}, [], profile)
    assert result.excluded is True


def test_frontmatter_private_false_not_excluded():
    profile = _Profile()
    result = is_private("note.md", {"private": False}, [], profile)
    assert result.excluded is False


def test_frontmatter_missing_not_excluded():
    profile = _Profile()
    result = is_private("note.md", {}, [], profile)
    assert result.excluded is False


# ── Tag checks ────────────────────────────────────────────────────────────────

def test_private_tag_excluded():
    profile = _Profile()
    result = is_private("note.md", {}, ["private", "work"], profile)
    assert result.excluded is True
    assert result.matched_mechanism == "tag"


def test_no_index_tag_excluded():
    profile = _Profile()
    result = is_private("note.md", {}, ["no-index"], profile)
    assert result.excluded is True


def test_normal_tags_not_excluded():
    profile = _Profile()
    result = is_private("note.md", {}, ["project", "important"], profile)
    assert result.excluded is False


# ── Mode: off ────────────────────────────────────────────────────────────────

def test_mode_off_nothing_excluded():
    profile = _Profile(privacy_filter_mode="off")
    result = is_private("private/secret.md", {"private": True}, ["no-index"], profile)
    assert result.excluded is False


# ── Mode: and ────────────────────────────────────────────────────────────────

def test_mode_and_requires_all_mechanisms():
    profile = _Profile(privacy_filter_mode="and")
    # Only path prefix matches — not all mechanisms
    result = is_private("private/note.md", {}, [], profile)
    assert result.excluded is False


def test_mode_and_all_match_excluded():
    profile = _Profile(privacy_filter_mode="and")
    result = is_private("private/note.md", {"private": True}, ["no-index"], profile)
    assert result.excluded is True
    assert result.matched_mechanism == "and"


# ── list_excluded ─────────────────────────────────────────────────────────────

def test_list_excluded_finds_private_files():
    profile = _Profile()
    files = scan(profile)
    excluded = list_excluded(profile, files)
    excluded_paths = {e["rel_path"] for e in excluded}
    assert "private/secret.md" in excluded_paths
    assert "_private/notes.md" in excluded_paths


def test_list_excluded_does_not_exclude_public_files():
    profile = _Profile()
    files = scan(profile)
    excluded = list_excluded(profile, files)
    excluded_paths = {e["rel_path"] for e in excluded}
    assert "index.md" not in excluded_paths
    assert "projects/Alpha.md" not in excluded_paths


def test_list_excluded_has_reason_and_mechanism():
    profile = _Profile()
    files = scan(profile)
    excluded = list_excluded(profile, files)
    for item in excluded:
        assert "rel_path" in item
        assert "reason" in item
        assert "mechanism" in item
