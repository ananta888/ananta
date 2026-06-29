"""Integration tests for Obsidian Vault adapter (OBS-012).

Tests the full pipeline: scan → privacy filter → extract → index records.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag-helper"))

from rag_helper.application.vault_scanner import scan, compute_diff, load_manifest
from rag_helper.application.privacy_filter import list_excluded, is_private
from rag_helper.extractors.obsidian import ObsidianExtractor, parse_frontmatter, extract_tags
from rag_helper.extractors.obsidian_canvas import CanvasExtractor
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.importance_scoring import compute_importance_score, score_index_records

VAULT_DIR = Path(__file__).parent / "fixtures" / "obsidian_test_vault"


class _VaultProfile:
    """Minimal vault profile for integration tests."""

    def __init__(self, **kwargs):
        self.path = str(VAULT_DIR)
        self.name = kwargs.get("name", "test_vault")
        self.enabled = True
        self.read_only = True
        self.exclude_dirs = [".obsidian", ".git", ".trash", "assets"]
        self.exclude_glob_patterns = []
        self.private_path_prefixes = ["private/", "_private"]
        self.private_frontmatter_field = "private"
        self.private_frontmatter_truthy_values = [True, "true", "yes", "1"]
        self.private_tags = ["no-index", "private"]
        self.privacy_filter_mode = "or"
        self.index_canvas_files = True
        self.heading_chunk_level = 2
        self.max_block_size_chars = 2000
        self.min_block_size_chars = 10


# ── OBS-012-002: Full scan + privacy filter ───────────────────────────────────

def test_full_scan_finds_all_files():
    profile = _VaultProfile()
    files = scan(profile)
    rel_paths = {f.rel_path for f in files}
    assert "index.md" in rel_paths
    assert "projects/Alpha.md" in rel_paths
    assert "projects/Beta.md" in rel_paths
    assert "overview.canvas" in rel_paths
    assert "private/secret.md" in rel_paths
    assert "_private/notes.md" in rel_paths


def test_privacy_filter_removes_correct_files():
    profile = _VaultProfile()
    files = scan(profile)
    excluded = list_excluded(profile, files)
    excluded_paths = {e["rel_path"] for e in excluded}
    # Private files must be excluded
    assert "private/secret.md" in excluded_paths
    assert "_private/notes.md" in excluded_paths
    # Public files must NOT be excluded
    assert "index.md" not in excluded_paths
    assert "projects/Alpha.md" not in excluded_paths
    assert "projects/Beta.md" not in excluded_paths


def test_public_files_after_filter():
    profile = _VaultProfile()
    files = scan(profile)
    excluded_paths = {e["rel_path"] for e in list_excluded(profile, files)}
    public_files = [f for f in files if f.rel_path not in excluded_paths]
    public_paths = {f.rel_path for f in public_files}
    assert "index.md" in public_paths
    assert "projects/Alpha.md" in public_paths


# ── OBS-012-003: Extract and index records ───────────────────────────────────

def test_extract_alpha_produces_index_and_detail():
    extractor = ObsidianExtractor(vault_name="test_vault", min_block_size_chars=10)
    alpha_text = (VAULT_DIR / "projects" / "Alpha.md").read_text()
    index, details, relations, stats = extractor.parse("projects/Alpha.md", alpha_text)

    assert len(index) >= 1  # at least the note record
    assert len(details) == 1

    note = next(r for r in index if r["kind"] == "obsidian_note")
    assert note["title"] == "Alpha Project"
    assert note["vault"] == "test_vault"
    assert note["source_type"] == "obsidian_vault"


def test_two_pass_link_resolution():
    """pre_scan_types in pass 1 → parse with known_note_titles in pass 2."""
    extractor = ObsidianExtractor(vault_name="test_vault")

    # Pass 1: build known_note_titles
    known_note_titles: dict[str, str] = {}
    vault_files_texts = {
        "projects/Alpha.md": (VAULT_DIR / "projects" / "Alpha.md").read_text(),
        "projects/Beta.md": (VAULT_DIR / "projects" / "Beta.md").read_text(),
        "index.md": (VAULT_DIR / "index.md").read_text(),
    }
    for rel_path, text in vault_files_texts.items():
        prescan = extractor.pre_scan_types(rel_path, text)
        title = prescan["title"].lower()
        known_note_titles[title] = rel_path
        for alias in prescan["aliases"]:
            known_note_titles[alias.lower()] = rel_path
        # Also add path without extension
        path_key = rel_path.lower()
        if path_key.endswith(".md"):
            path_key = path_key[:-3]
        known_note_titles[path_key] = rel_path

    # Pass 2: parse index.md with link resolution
    index_text = vault_files_texts["index.md"]
    _, _, relations, stats = extractor.parse("index.md", index_text, known_package_types=known_note_titles)

    resolved = [r for r in relations if r.get("type") == "obs_wikilink" and r.get("resolved")]
    assert len(resolved) >= 1, f"Expected resolved links, got relations: {relations[:5]}"


# ── OBS-012-004: ProcessingLimits new fields ─────────────────────────────────

def test_processing_limits_obsidian_fields():
    limits = ProcessingLimits()
    assert limits.md_heading_chunk_level == 2
    assert limits.md_max_block_size_chars == 2000
    assert limits.md_min_block_size_chars == 50
    assert limits.md_max_headings_per_note is None
    assert limits.md_max_links_per_note == 200
    assert limits.canvas_max_nodes is None


def test_processing_limits_custom_obsidian_values():
    limits = ProcessingLimits(
        md_heading_chunk_level=3,
        md_max_block_size_chars=1000,
        md_min_block_size_chars=20,
        canvas_max_nodes=50,
    )
    assert limits.md_heading_chunk_level == 3
    assert limits.canvas_max_nodes == 50


# ── OBS-012-005: Importance scoring for obsidian kinds ───────────────────────

def test_importance_score_obsidian_note():
    record = {"kind": "obsidian_note", "tags": []}
    score = compute_importance_score(record)
    assert score > 1.0  # base + 0.5


def test_importance_score_obsidian_block():
    record = {"kind": "obsidian_block", "tags": []}
    score = compute_importance_score(record)
    assert score > 1.0  # base + 0.7


def test_importance_score_obsidian_important_tag():
    record = {"kind": "obsidian_note", "tags": ["important", "work"]}
    score = compute_importance_score(record)
    base_note_score = compute_importance_score({"kind": "obsidian_note", "tags": []})
    assert score > base_note_score  # +0.1 for important tag


def test_score_index_records_applies_scores():
    records = [
        {"kind": "obsidian_note", "tags": [], "id": "n1"},
        {"kind": "obsidian_block", "tags": [], "id": "b1"},
    ]
    score_index_records(records, "basic")
    assert "importance_score" in records[0]
    assert "importance_score" in records[1]
    assert records[1]["importance_score"] > records[0]["importance_score"]


# ── OBS-012-006: Canvas integration ──────────────────────────────────────────

def test_canvas_integration_extract():
    extractor = CanvasExtractor(vault_name="test_vault")
    canvas_text = (VAULT_DIR / "overview.canvas").read_text()
    index, details, relations, stats = extractor.parse("overview.canvas", canvas_text)

    assert any(r["kind"] == "obsidian_canvas" for r in index)
    canvas_rec = next(r for r in index if r["kind"] == "obsidian_canvas")
    assert canvas_rec["vault"] == "test_vault"
    assert canvas_rec["node_count"] == 2


# ── OBS-012-007: Full pipeline with build_extractors ─────────────────────────

def test_build_extractors_obsidian_registration():
    from rag_helper.application.document_extractor import build_extractors

    # Minimal stubs for required extractors
    class _StubExtractor:
        def __init__(self, **kwargs):
            pass
        def parse(self, rel_path, text, **kwargs):
            return [], [], [], {}
        def pre_scan_types(self, rel_path, text):
            return {}

    limits = ProcessingLimits()
    extractors = build_extractors(
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        limits=limits,
        java_extractor_cls=_StubExtractor,
        adoc_extractor_cls=_StubExtractor,
        xml_extractor_cls=_StubExtractor,
        xsd_extractor_cls=_StubExtractor,
        obsidian_extractor_cls=ObsidianExtractor,
        obsidian_canvas_extractor_cls=CanvasExtractor,
        obsidian_vault_name="vault1",
    )
    assert "md" in extractors
    assert "canvas" in extractors
    assert isinstance(extractors["md"], ObsidianExtractor)
    assert isinstance(extractors["canvas"], CanvasExtractor)
