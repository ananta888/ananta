"""Tests for ObsidianCanvasExtractor (OBS-006)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag-helper"))

from rag_helper.extractors.obsidian_canvas import CanvasExtractor

VAULT_DIR = Path(__file__).parent / "fixtures" / "obsidian_test_vault"
CANVAS_TEXT = (VAULT_DIR / "overview.canvas").read_text(encoding="utf-8")


def test_canvas_parse_returns_4_tuple():
    extractor = CanvasExtractor(vault_name="test")
    result = extractor.parse("overview.canvas", CANVAS_TEXT)
    assert len(result) == 4


def test_canvas_parse_canvas_index_record():
    extractor = CanvasExtractor(vault_name="test")
    index, details, relations, stats = extractor.parse("overview.canvas", CANVAS_TEXT)
    canvas_records = [r for r in index if r["kind"] == "obsidian_canvas"]
    assert len(canvas_records) == 1
    canvas = canvas_records[0]
    assert canvas["id"].startswith("obs_canvas:")
    assert canvas["vault"] == "test"
    assert canvas["file"] == "overview.canvas"
    assert canvas["title"] == "overview"
    assert canvas["node_count"] == 2
    assert canvas["edge_count"] == 1
    assert canvas["source_type"] == "obsidian_vault"


def test_canvas_parse_node_records():
    extractor = CanvasExtractor(vault_name="test")
    index, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    node_records = [r for r in index if r["kind"] == "obsidian_canvas_node"]
    assert len(node_records) == 2


def test_canvas_node_types():
    extractor = CanvasExtractor(vault_name="test")
    index, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    node_records = [r for r in index if r["kind"] == "obsidian_canvas_node"]
    types = {n["node_type"] for n in node_records}
    assert "text" in types
    assert "file" in types


def test_canvas_text_node_content():
    extractor = CanvasExtractor(vault_name="test")
    index, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    text_nodes = [n for n in index if n.get("node_type") == "text"]
    assert len(text_nodes) == 1
    assert text_nodes[0]["text"] == "Vault Overview"


def test_canvas_file_node_path():
    extractor = CanvasExtractor(vault_name="test")
    index, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    file_nodes = [n for n in index if n.get("node_type") == "file"]
    assert len(file_nodes) == 1
    assert file_nodes[0]["file"] == "projects/Alpha.md"


def test_canvas_detail_record():
    extractor = CanvasExtractor(vault_name="test")
    _, details, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    assert len(details) == 1
    detail = details[0]
    assert detail["kind"] == "obsidian_canvas_detail"
    assert len(detail["nodes"]) == 2
    assert len(detail["edges"]) == 1


def test_canvas_edge_relations():
    extractor = CanvasExtractor(vault_name="test")
    _, _, relations, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    edge_rels = [r for r in relations if r["type"] == "obs_canvas_edge"]
    assert len(edge_rels) == 1
    assert edge_rels[0]["label"] == "references"


def test_canvas_file_node_relation_to_note():
    extractor = CanvasExtractor(vault_name="test")
    _, _, relations, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    note_rels = [r for r in relations if r["type"] == "obs_canvas_references_note"]
    assert len(note_rels) == 1
    assert note_rels[0]["resolved"] is True


def test_canvas_stats():
    extractor = CanvasExtractor(vault_name="test")
    _, _, _, stats = extractor.parse("overview.canvas", CANVAS_TEXT)
    assert stats["node_count"] == 2
    assert stats["edge_count"] == 1
    assert "graph_nodes" in stats
    assert len(stats["graph_nodes"]) >= 1


def test_canvas_invalid_json():
    extractor = CanvasExtractor(vault_name="test")
    index, details, relations, stats = extractor.parse("bad.canvas", "not json {{{")
    assert index == []
    assert "error" in stats


def test_canvas_empty_canvas():
    extractor = CanvasExtractor(vault_name="test")
    canvas_json = json.dumps({"nodes": [], "edges": []})
    index, details, relations, stats = extractor.parse("empty.canvas", canvas_json)
    canvas_records = [r for r in index if r["kind"] == "obsidian_canvas"]
    assert len(canvas_records) == 1
    assert stats["node_count"] == 0


def test_canvas_max_nodes_limit():
    extractor = CanvasExtractor(vault_name="test", max_nodes=1)
    index, _, _, stats = extractor.parse("overview.canvas", CANVAS_TEXT)
    assert stats["node_count"] == 1


def test_canvas_id_stability():
    extractor = CanvasExtractor(vault_name="test")
    index1, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    index2, _, _, _ = extractor.parse("overview.canvas", CANVAS_TEXT)
    ids1 = {r["id"] for r in index1}
    ids2 = {r["id"] for r in index2}
    assert ids1 == ids2
