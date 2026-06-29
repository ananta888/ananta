"""Tests for ObsidianVaultScanner (OBS-002)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag-helper"))

from rag_helper.application.vault_scanner import (
    VaultFile,
    VaultDiff,
    compute_diff,
    load_manifest,
    save_manifest,
    scan,
    update_manifest_entry,
)

VAULT_DIR = Path(__file__).parent / "fixtures" / "obsidian_test_vault"


class _FakeProfile:
    def __init__(self, path, **kwargs):
        self.path = str(path)
        self.exclude_dirs = kwargs.get("exclude_dirs", [".obsidian", ".git", ".trash", "assets"])
        self.exclude_glob_patterns = kwargs.get("exclude_glob_patterns", [])
        self.index_canvas_files = kwargs.get("index_canvas_files", True)
        self.private_path_prefixes = kwargs.get("private_path_prefixes", ["private/", "_private"])
        self.private_frontmatter_field = "private"
        self.private_frontmatter_truthy_values = [True, "true", "yes", "1"]
        self.private_tags = ["no-index", "private"]
        self.privacy_filter_mode = "or"


def test_scan_returns_vault_files():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    assert len(files) > 0
    for vf in files:
        assert isinstance(vf, VaultFile)
        assert vf.rel_path
        assert vf.abs_path
        assert vf.ext in ("md", "canvas")


def test_scan_finds_all_expected_files():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    rel_paths = {vf.rel_path for vf in files}
    assert "index.md" in rel_paths
    assert "projects/Alpha.md" in rel_paths
    assert "projects/Beta.md" in rel_paths
    assert "overview.canvas" in rel_paths


def test_scan_includes_private_files():
    """Scanner does NOT filter privacy - that's the privacy filter's job."""
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    rel_paths = {vf.rel_path for vf in files}
    # Private files should still be found by scanner
    assert "private/secret.md" in rel_paths


def test_scan_excludes_by_dir():
    """Dirs in exclude_dirs should be skipped."""
    profile = _FakeProfile(VAULT_DIR, exclude_dirs=[".obsidian", "projects"])
    files = scan(profile)
    rel_paths = {vf.rel_path for vf in files}
    assert not any(r.startswith("projects/") for r in rel_paths)


def test_scan_excludes_canvas_when_disabled():
    profile = _FakeProfile(VAULT_DIR, index_canvas_files=False)
    files = scan(profile)
    assert not any(vf.ext == "canvas" for vf in files)


def test_scan_files_sorted():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    paths = [vf.rel_path for vf in files]
    assert paths == sorted(paths)


def test_vault_file_has_sha256():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    for vf in files:
        assert vf.sha256  # non-empty
        assert len(vf.sha256) == 64  # SHA-256 hex


# ── Manifest ─────────────────────────────────────────────────────────────────

def test_save_and_load_manifest(tmp_path):
    manifest = {"index.md": {"sha256": "abc123", "mtime": 1000.0, "last_indexed_at": 2000.0, "indexed_record_count": 5}}
    path = tmp_path / "vault_manifest.json"
    save_manifest(manifest, path)
    loaded = load_manifest(path)
    assert loaded == manifest


def test_load_manifest_missing_file(tmp_path):
    path = tmp_path / "nonexistent.json"
    result = load_manifest(path)
    assert result == {}


def test_save_manifest_atomic(tmp_path):
    """save_manifest should not leave .tmp files behind."""
    path = tmp_path / "manifest.json"
    save_manifest({"a": {"sha256": "x"}}, path)
    assert path.exists()
    assert not (tmp_path / "manifest.tmp").exists()


# ── compute_diff ─────────────────────────────────────────────────────────────

def test_compute_diff_all_new():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    diff = compute_diff(files, {})
    assert len(diff.new) == len(files)
    assert diff.changed == []
    assert diff.deleted == []
    assert diff.unchanged == []


def test_compute_diff_all_unchanged():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    manifest = {vf.rel_path: {"sha256": vf.sha256, "mtime": vf.mtime} for vf in files}
    diff = compute_diff(files, manifest)
    assert diff.new == []
    assert diff.changed == []
    assert diff.deleted == []
    assert len(diff.unchanged) == len(files)


def test_compute_diff_detects_changed():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    manifest = {vf.rel_path: {"sha256": "old_hash", "mtime": vf.mtime} for vf in files}
    diff = compute_diff(files, manifest)
    assert len(diff.changed) == len(files)
    assert diff.new == []


def test_compute_diff_detects_deleted():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    manifest = {
        vf.rel_path: {"sha256": vf.sha256, "mtime": vf.mtime} for vf in files
    }
    manifest["deleted_file.md"] = {"sha256": "xyz", "mtime": 0.0}
    diff = compute_diff(files, manifest)
    assert "deleted_file.md" in diff.deleted


# ── update_manifest_entry ────────────────────────────────────────────────────

def test_update_manifest_entry():
    profile = _FakeProfile(VAULT_DIR)
    files = scan(profile)
    vf = files[0]
    manifest: dict = {}
    update_manifest_entry(manifest, vf, indexed_record_count=3)
    entry = manifest[vf.rel_path]
    assert entry["sha256"] == vf.sha256
    assert entry["indexed_record_count"] == 3
    assert "last_indexed_at" in entry
