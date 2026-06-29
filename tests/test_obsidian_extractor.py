"""Tests for ObsidianExtractor (OBS-004, OBS-007, OBS-008)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag-helper"))

from rag_helper.extractors.obsidian import (
    ObsidianExtractor,
    chunk_by_headings,
    extract_codeblocks,
    extract_headings,
    extract_tags,
    extract_wikilinks,
    heading_to_anchor,
    parse_frontmatter,
    resolve_link,
)

VAULT_DIR = Path(__file__).parent / "fixtures" / "obsidian_test_vault"

ALPHA_MD = (VAULT_DIR / "projects" / "Alpha.md").read_text(encoding="utf-8")
INDEX_MD = (VAULT_DIR / "index.md").read_text(encoding="utf-8")
BETA_MD = (VAULT_DIR / "projects" / "Beta.md").read_text(encoding="utf-8")


# ── parse_frontmatter ─────────────────────────────────────────────────────────

def test_parse_frontmatter_basic():
    text = "---\ntitle: Hello\ntags: [a, b]\n---\n# Body"
    fm, body = parse_frontmatter(text)
    assert fm["title"] == "Hello"
    assert fm["tags"] == ["a", "b"]
    assert "# Body" in body


def test_parse_frontmatter_no_frontmatter():
    text = "# Just a heading\nsome text"
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert "# Just a heading" in body


def test_parse_frontmatter_empty_frontmatter():
    text = "---\n---\n# Body"
    fm, body = parse_frontmatter(text)
    assert fm == {}


def test_parse_frontmatter_alpha():
    fm, body = parse_frontmatter(ALPHA_MD)
    assert fm["title"] == "Alpha Project"
    assert "project-alpha" in fm["aliases"]
    assert "project" in fm["tags"]


# ── extract_headings ──────────────────────────────────────────────────────────

def test_extract_headings_basic():
    body = "# H1\n## H2\n### H3\nsome text"
    headings = extract_headings(body)
    assert len(headings) == 3
    assert headings[0]["level"] == 1
    assert headings[0]["title"] == "H1"
    assert headings[1]["level"] == 2


def test_extract_headings_skips_code_blocks():
    body = "# Real Heading\n```python\n## Not a heading\n```\n## Another Real Heading"
    headings = extract_headings(body)
    titles = [h["title"] for h in headings]
    assert "Real Heading" in titles
    assert "Another Real Heading" in titles
    assert "Not a heading" not in titles


def test_extract_headings_alpha():
    _, body = parse_frontmatter(ALPHA_MD)
    headings = extract_headings(body)
    titles = [h["title"] for h in headings]
    assert "Alpha Project" in titles
    assert "Goals" in titles
    # Code block heading should NOT be extracted
    assert "Not a heading inside code" not in titles


# ── heading_to_anchor ─────────────────────────────────────────────────────────

def test_heading_to_anchor_basic():
    assert heading_to_anchor("Hello World") == "hello-world"


def test_heading_to_anchor_special_chars():
    anchor = heading_to_anchor("Über Design & Architecture")
    assert "design" in anchor
    assert "architecture" in anchor


# ── extract_wikilinks ─────────────────────────────────────────────────────────

def test_extract_wikilinks_simple():
    body = "See [[Note A]] and [[Note B]]."
    links = extract_wikilinks(body)
    targets = [l["target"] for l in links]
    assert "Note A" in targets
    assert "Note B" in targets


def test_extract_wikilinks_with_display():
    body = "[[Target|Display Text]]"
    links = extract_wikilinks(body)
    assert links[0]["target"] == "Target"
    assert links[0]["display"] == "Display Text"


def test_extract_wikilinks_with_heading():
    body = "[[Note#Section]]"
    links = extract_wikilinks(body)
    assert links[0]["target"] == "Note"
    assert links[0]["heading"] == "Section"


def test_extract_wikilinks_complex():
    body = "[[Note#Section|Display]]"
    links = extract_wikilinks(body)
    assert links[0]["target"] == "Note"
    assert links[0]["heading"] == "Section"
    assert links[0]["display"] == "Display"


def test_extract_wikilinks_index():
    _, body = parse_frontmatter(INDEX_MD)
    links = extract_wikilinks(body)
    targets = [l["target"] for l in links]
    assert "projects/Alpha" in targets
    assert "projects/Beta" in targets
    assert "NonExistent" in targets


def test_extract_wikilinks_skips_code_blocks():
    body = "Normal [[Link]]\n```\n[[CodeLink]]\n```"
    links = extract_wikilinks(body)
    targets = [l["target"] for l in links]
    assert "Link" in targets
    assert "CodeLink" not in targets


# ── extract_tags ──────────────────────────────────────────────────────────────

def test_extract_tags_from_frontmatter():
    fm = {"tags": ["project", "important"]}
    tags = extract_tags(fm, "")
    assert "project" in tags
    assert "important" in tags


def test_extract_tags_inline():
    fm = {}
    body = "This is #project and #important work."
    tags = extract_tags(fm, body)
    assert "project" in tags
    assert "important" in tags


def test_extract_tags_deduplication():
    fm = {"tags": ["project"]}
    body = "#project #other"
    tags = extract_tags(fm, body)
    assert tags.count("project") == 1


def test_extract_tags_alpha():
    fm, body = parse_frontmatter(ALPHA_MD)
    tags = extract_tags(fm, body)
    assert "project" in tags
    assert "important" in tags


# ── extract_codeblocks ────────────────────────────────────────────────────────

def test_extract_codeblocks_basic():
    body = "text\n```python\ncode here\n```\nmore text"
    blocks = extract_codeblocks(body)
    assert len(blocks) == 1
    assert blocks[0]["language"] == "python"
    assert "code here" in blocks[0]["content"]


def test_extract_codeblocks_alpha():
    _, body = parse_frontmatter(ALPHA_MD)
    blocks = extract_codeblocks(body)
    assert len(blocks) >= 1
    assert blocks[0]["language"] == "python"


def test_extract_codeblocks_multiple():
    body = "```\nblock1\n```\ntext\n```python\nblock2\n```"
    blocks = extract_codeblocks(body)
    assert len(blocks) == 2


# ── chunk_by_headings ─────────────────────────────────────────────────────────

def test_chunk_by_headings_basic():
    body = "intro\n## Section A\ncontent A\n## Section B\ncontent B"
    headings = extract_headings(body)
    chunks = chunk_by_headings(body, headings, chunk_level=2, min_block_size_chars=1)
    # Should have intro + A + B = 3 or some variant
    assert len(chunks) >= 1
    contents = [c["content"] for c in chunks]
    joined = " ".join(contents)
    assert "content A" in joined or "Section A" in joined


def test_chunk_by_headings_no_headings():
    body = "just some text with enough content here"
    chunks = chunk_by_headings(body, [], min_block_size_chars=1)
    assert len(chunks) == 1
    assert chunks[0]["heading_path"] == ""


def test_chunk_by_headings_respects_max_size():
    body = "## Section\n" + "x" * 5000
    headings = extract_headings(body)
    chunks = chunk_by_headings(body, headings, chunk_level=2, max_block_size_chars=2000, min_block_size_chars=1)
    for chunk in chunks:
        assert len(chunk["content"]) <= 2000


def test_chunk_by_headings_respects_min_size():
    body = "## Big section\n" + "x" * 100 + "\n## Tiny\nhi"
    headings = extract_headings(body)
    chunks = chunk_by_headings(body, headings, chunk_level=2, min_block_size_chars=50)
    contents = [c["content"] for c in chunks]
    # "hi" is only 2 chars, should be excluded (under min_block_size_chars)
    assert not any(c.strip() == "## Tiny\nhi" for c in contents)


# ── pre_scan_types ────────────────────────────────────────────────────────────

def test_pre_scan_types_title_from_frontmatter():
    extractor = ObsidianExtractor(vault_name="test")
    result = extractor.pre_scan_types("projects/Alpha.md", ALPHA_MD)
    assert result["title"] == "Alpha Project"


def test_pre_scan_types_title_from_filename():
    extractor = ObsidianExtractor(vault_name="test")
    text = "# Some Content\nno frontmatter title"
    result = extractor.pre_scan_types("my_note.md", text)
    assert result["title"] == "my_note"


def test_pre_scan_types_aliases():
    extractor = ObsidianExtractor(vault_name="test")
    result = extractor.pre_scan_types("projects/Alpha.md", ALPHA_MD)
    assert "Alpha Project" in result["aliases"]
    assert "project-alpha" in result["aliases"]


# ── parse ─────────────────────────────────────────────────────────────────────

def test_parse_returns_4_tuple():
    extractor = ObsidianExtractor(vault_name="test")
    result = extractor.parse("index.md", INDEX_MD)
    assert len(result) == 4
    index, details, relations, stats = result


def test_parse_note_index_record():
    extractor = ObsidianExtractor(vault_name="test")
    index, details, relations, stats = extractor.parse("index.md", INDEX_MD)
    note_records = [r for r in index if r["kind"] == "obsidian_note"]
    assert len(note_records) == 1
    note = note_records[0]
    assert note["id"].startswith("obs_note:")
    assert note["vault"] == "test"
    assert note["file"] == "index.md"
    assert note["title"] == "Index"
    assert "index" in note["tags"]
    assert note["source_type"] == "obsidian_vault"


def test_parse_block_index_records():
    extractor = ObsidianExtractor(vault_name="test", min_block_size_chars=1)
    index, _, _, stats = extractor.parse("projects/Alpha.md", ALPHA_MD)
    block_records = [r for r in index if r["kind"] == "obsidian_block"]
    assert len(block_records) >= 1
    block = block_records[0]
    assert block["id"].startswith("obs_block:")
    assert block["parent_id"].startswith("obs_note:")
    assert block["vault"] == "test"


def test_parse_detail_record():
    extractor = ObsidianExtractor(vault_name="test")
    _, details, _, _ = extractor.parse("index.md", INDEX_MD)
    assert len(details) == 1
    detail = details[0]
    assert detail["kind"] == "obsidian_note_detail"
    assert detail["link_count"] >= 2
    assert detail["title"] == "Index"
    assert "frontmatter" in detail
    assert "full_content" in detail


def test_parse_relation_records_wikilinks():
    # Set up known_note_titles for index.md
    extractor = ObsidianExtractor(vault_name="test")
    known = {
        "projects/alpha": "projects/Alpha.md",
        "projects/beta": "projects/Beta.md",
    }
    _, _, relations, _ = extractor.parse("index.md", INDEX_MD, known_package_types=known)
    types = {r["type"] for r in relations}
    assert "obs_wikilink" in types or "obs_wikilink_unresolved" in types


def test_parse_relation_unresolved_link():
    extractor = ObsidianExtractor(vault_name="test")
    _, _, relations, _ = extractor.parse("index.md", INDEX_MD, known_package_types={})
    unresolved = [r for r in relations if r["type"] == "obs_wikilink_unresolved"]
    assert len(unresolved) >= 1


def test_parse_relation_tag_relations():
    extractor = ObsidianExtractor(vault_name="test")
    _, _, relations, _ = extractor.parse("projects/Alpha.md", ALPHA_MD)
    tag_rels = [r for r in relations if r["type"] == "obs_has_tag"]
    assert len(tag_rels) >= 1
    assert any("project" in r["to"] for r in tag_rels)


def test_parse_stats():
    extractor = ObsidianExtractor(vault_name="test")
    _, _, _, stats = extractor.parse("projects/Alpha.md", ALPHA_MD)
    assert "heading_count" in stats
    assert "link_count" in stats
    assert "tag_count" in stats
    assert "block_count" in stats
    assert "graph_nodes" in stats
    assert isinstance(stats["graph_nodes"], list)


def test_parse_id_stability():
    """Same file → same IDs on repeated calls."""
    extractor = ObsidianExtractor(vault_name="test")
    index1, _, _, _ = extractor.parse("index.md", INDEX_MD)
    index2, _, _, _ = extractor.parse("index.md", INDEX_MD)
    ids1 = {r["id"] for r in index1}
    ids2 = {r["id"] for r in index2}
    assert ids1 == ids2


def test_parse_link_resolution():
    """When known_note_titles contains the target, link should be resolved."""
    extractor = ObsidianExtractor(vault_name="vault1")
    # Build known titles: normalized title -> rel_path
    known = {"projects/alpha": "projects/Alpha.md"}
    _, _, relations, _ = extractor.parse("index.md", INDEX_MD, known_package_types=known)
    resolved = [r for r in relations if r.get("resolved") is True]
    assert len(resolved) >= 1


def test_parse_max_links_limit():
    extractor = ObsidianExtractor(vault_name="test", max_links_per_note=1)
    # Create text with many links
    text = "---\ntitle: Many Links\n---\n" + " ".join(f"[[Link{i}]]" for i in range(10))
    _, _, _, stats = extractor.parse("many.md", text)
    assert stats["skipped_links"] > 0
