"""Obsidian Markdown Extractor for CodeCompass/ANANTA (OBS-004).

Implements JavaLikeExtractor Protocol:
  pre_scan_types(rel_path, text) -> dict   — builds title+alias map
  parse(rel_path, text, known_package_types) -> (index, details, relations, stats)
"""
from __future__ import annotations

import re
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

from rag_helper.utils.ids import sha1_text


# ─── Regex patterns ──────────────────────────────────────────────────────────

WIKILINK_RE = re.compile(r'\[\[([^\[\]]+)\]\]')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
INLINE_TAG_RE = re.compile(r'(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)(?!\w)')
DATAVIEW_BLOCK_RE = re.compile(
    r'```dataview\s*\n(.*?)```',
    re.DOTALL | re.IGNORECASE,
)
DATAVIEW_INLINE_RE = re.compile(r'`=\s*(.+?)`')


# ─── Frontmatter ─────────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter between --- delimiters.

    Returns (frontmatter_dict, body_text).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    try:
        if _YAML_AVAILABLE:
            result = yaml.safe_load(fm_text)
        else:
            result = None
        if isinstance(result, dict):
            return result, body
        return {}, body
    except Exception:
        return {}, body


# ─── Headings ────────────────────────────────────────────────────────────────

def _is_code_fence(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def extract_headings(body: str) -> list[dict]:
    """Extract heading tree from body text (skipping code blocks)."""
    headings = []
    in_code = False
    lines = body.split("\n")
    char_offset = 0

    for line in lines:
        if _is_code_fence(line):
            in_code = not in_code
        elif not in_code:
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                headings.append({
                    "level": level,
                    "title": title,
                    "char_offset": char_offset,
                })
        char_offset += len(line) + 1  # +1 for newline

    return headings


def heading_to_anchor(heading_text: str) -> str:
    """Convert heading text to Obsidian-style anchor."""
    anchor = heading_text.lower()
    anchor = re.sub(r'[^\w\s-]', '', anchor)
    anchor = re.sub(r'\s+', '-', anchor.strip())
    return anchor


def build_heading_path(headings: list[dict], up_to_idx: int) -> str:
    """Build breadcrumb path like 'H1 > H2 > H3' for heading at up_to_idx."""
    if up_to_idx >= len(headings):
        return ""
    target = headings[up_to_idx]
    target_level = target["level"]

    path_parts = []
    # Walk backwards to build hierarchy
    current_level = target_level
    for i in range(up_to_idx, -1, -1):
        h = headings[i]
        if h["level"] <= current_level:
            path_parts.append(h["title"])
            current_level = h["level"] - 1
        if current_level <= 0:
            break

    path_parts.reverse()
    return " > ".join(path_parts)


# ─── Wiki-Links ──────────────────────────────────────────────────────────────

def extract_wikilinks(body: str) -> list[dict]:
    """Extract all wikilinks from body text.

    Supports: [[Target]], [[Target#Heading]], [[Target|Display]], [[Target#Heading|Display]]
    """
    links = []
    in_code = False
    for line in body.split("\n"):
        if _is_code_fence(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in WIKILINK_RE.finditer(line):
            raw = m.group(1)
            display = None
            heading = None
            target = raw

            if "|" in raw:
                target, display = raw.split("|", 1)
            if "#" in target:
                target, heading = target.split("#", 1)

            target = target.strip()
            display = display.strip() if display else None
            heading = heading.strip() if heading else None

            links.append({
                "target": target,
                "heading": heading,
                "display": display,
                "raw": m.group(0),
            })
    return links


def resolve_link(
    wikilink: dict,
    known_note_titles: dict[str, str],
    vault_name: str,
) -> dict:
    """Resolve a wikilink to a target note ID.

    known_note_titles: {normalized_title_or_alias -> rel_path}
    Returns a dict with 'resolved', 'target_id', 'confidence'.
    """
    target = wikilink["target"]
    target_lower = target.lower()

    # Exact match
    if target_lower in known_note_titles:
        rel_path = known_note_titles[target_lower]
        target_id = f"obs_note:{sha1_text(f'{vault_name}:{rel_path}')}"
        return {"resolved": True, "target_id": target_id, "confidence": 1.0}

    # Partial match: last component of path
    for key, rel_path in known_note_titles.items():
        note_name = key.split("/")[-1]
        if note_name == target_lower:
            target_id = f"obs_note:{sha1_text(f'{vault_name}:{rel_path}')}"
            return {"resolved": True, "target_id": target_id, "confidence": 0.8}

    return {
        "resolved": False,
        "target_id": f"obs_unresolved:{target}",
        "confidence": 0.0,
    }


# ─── Tags ────────────────────────────────────────────────────────────────────

def extract_tags(frontmatter: dict, body: str) -> list[str]:
    """Extract tags from frontmatter 'tags' field and inline #tags in body."""
    tags: list[str] = []
    seen: set[str] = set()

    # From frontmatter
    fm_tags = frontmatter.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]
    for t in fm_tags:
        normalized = str(t).lower().lstrip("#").strip()
        if normalized and normalized not in seen:
            tags.append(normalized)
            seen.add(normalized)

    # Inline tags from body (skip code blocks)
    in_code = False
    for line in body.split("\n"):
        if _is_code_fence(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in INLINE_TAG_RE.finditer(line):
            normalized = m.group(1).lower().strip()
            if normalized and normalized not in seen:
                tags.append(normalized)
                seen.add(normalized)

    return tags


# ─── Code blocks ─────────────────────────────────────────────────────────────

def extract_codeblocks(body: str) -> list[dict]:
    """Extract ``` and ~~~ delimited code blocks."""
    blocks = []
    lines = body.split("\n")
    in_block = False
    fence_char = None
    lang = ""
    block_lines: list[str] = []
    start_line = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_block:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence_char = stripped[:3]
                lang = stripped[3:].strip()
                in_block = True
                block_lines = []
                start_line = i
        else:
            if stripped.startswith(fence_char or "```"):
                blocks.append({
                    "language": lang,
                    "content": "\n".join(block_lines),
                    "start_line": start_line,
                    "end_line": i,
                })
                in_block = False
                fence_char = None
                lang = ""
                block_lines = []
            else:
                block_lines.append(line)

    return blocks


# ─── Dataview blocks ─────────────────────────────────────────────────────────

def extract_dataview_blocks(body: str) -> list[dict]:
    """Extract dataview code blocks as metadata."""
    blocks = []
    for m in DATAVIEW_BLOCK_RE.finditer(body):
        raw_query = m.group(1).strip()
        # Determine query type: TABLE, LIST, TASK, CALENDAR -> DQL; otherwise JS
        first_word = raw_query.split()[0].upper() if raw_query.split() else ""
        q_type = "dql" if first_word in ("TABLE", "LIST", "TASK", "CALENDAR") else "js"
        blocks.append({
            "query_type": q_type,
            "raw_query": raw_query,
        })
    return blocks


# ─── Section Chunker ─────────────────────────────────────────────────────────

def chunk_by_headings(
    body: str,
    headings: list[dict],
    chunk_level: int = 2,
    max_block_size_chars: int = 2000,
    min_block_size_chars: int = 50,
) -> list[dict]:
    """Split body into sections at the given heading level.

    Returns list of {heading_path, content, start_offset, end_offset}.
    """
    if not headings:
        # No headings: return entire body as single block
        content = body.strip()
        if len(content) >= min_block_size_chars:
            return [{"heading_path": "", "content": content}]
        return []

    # Find split points at chunk_level
    split_headings = [h for h in headings if h["level"] <= chunk_level]

    if not split_headings:
        # All headings are deeper: use first-level headings as splits
        split_headings = [h for h in headings if h["level"] == headings[0]["level"]]

    chunks = []

    # Content before first split heading
    if split_headings:
        first_offset = split_headings[0]["char_offset"]
        intro = body[:first_offset].strip()
        if len(intro) >= min_block_size_chars:
            chunks.append({"heading_path": "", "content": intro})

    for i, sh in enumerate(split_headings):
        start = sh["char_offset"]
        end = split_headings[i + 1]["char_offset"] if i + 1 < len(split_headings) else len(body)
        # Build heading path
        h_idx = headings.index(sh)
        heading_path = build_heading_path(headings, h_idx)
        content = body[start:end].strip()

        # Truncate oversized blocks
        if len(content) > max_block_size_chars:
            content = content[:max_block_size_chars]

        if len(content) >= min_block_size_chars:
            chunks.append({"heading_path": heading_path, "content": content})

    return chunks


# ─── Main Extractor ──────────────────────────────────────────────────────────

class ObsidianExtractor:
    """Extracts index, detail, and relation records from Obsidian Markdown notes.

    vault_name: identifier for this vault (used in IDs)
    heading_chunk_level: headings at this level cause a new block
    max_block_size_chars / min_block_size_chars: block size limits
    """

    def __init__(
        self,
        vault_name: str = "default",
        heading_chunk_level: int = 2,
        max_block_size_chars: int = 2000,
        min_block_size_chars: int = 50,
        max_links_per_note: int | None = 200,
        max_headings_per_note: int | None = None,
        index_dataview_as_metadata: bool = True,
    ) -> None:
        self.vault_name = vault_name
        self.heading_chunk_level = heading_chunk_level
        self.max_block_size_chars = max_block_size_chars
        self.min_block_size_chars = min_block_size_chars
        self.max_links_per_note = max_links_per_note
        self.max_headings_per_note = max_headings_per_note
        self.index_dataview_as_metadata = index_dataview_as_metadata

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        """First-pass scan: extract title and aliases for link resolution map.

        Returns dict with 'title' (str) and 'aliases' (list[str]).
        """
        frontmatter, _ = parse_frontmatter(text)
        # Title: from frontmatter > filename
        title = frontmatter.get("title") or ""
        if not title:
            # Derive from filename
            fname = rel_path.rsplit("/", 1)[-1]
            if fname.endswith(".md"):
                fname = fname[:-3]
            title = fname

        aliases: list[str] = []
        raw_aliases = frontmatter.get("aliases") or []
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        for a in raw_aliases:
            if a:
                aliases.append(str(a).strip())

        return {"title": title, "aliases": aliases}

    def parse(
        self,
        rel_path: str,
        text: str,
        known_package_types: dict | None = None,  # link map: normalized_title -> rel_path
    ) -> tuple[list[dict], list[dict], list[dict], dict]:
        """Full parse of a Markdown note.

        known_package_types: reused as known_note_titles map
          {normalized_title_or_alias -> rel_path}

        Returns (index_records, detail_records, relation_records, stats)
        """
        known_note_titles: dict[str, str] = known_package_types or {}
        vault_name = self.vault_name

        frontmatter, body = parse_frontmatter(text)
        headings = extract_headings(body)
        if self.max_headings_per_note is not None:
            headings = headings[: self.max_headings_per_note]

        wikilinks = extract_wikilinks(body)
        skipped_links = 0
        if self.max_links_per_note is not None and len(wikilinks) > self.max_links_per_note:
            skipped_links = len(wikilinks) - self.max_links_per_note
            wikilinks = wikilinks[: self.max_links_per_note]

        tags = extract_tags(frontmatter, body)
        dataview_blocks = extract_dataview_blocks(body) if self.index_dataview_as_metadata else []

        # ── Title ──────────────────────────────────────────────────────────
        title: str = frontmatter.get("title") or ""
        if not title:
            fname = rel_path.rsplit("/", 1)[-1]
            if fname.endswith(".md"):
                fname = fname[:-3]
            title = fname

        aliases: list[str] = []
        raw_aliases = frontmatter.get("aliases") or []
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        for a in raw_aliases:
            if a:
                aliases.append(str(a).strip())

        # ── IDs ────────────────────────────────────────────────────────────
        note_id = f"obs_note:{sha1_text(f'{vault_name}:{rel_path}')}"

        # ── Index: Note record ─────────────────────────────────────────────
        note_index = {
            "id": note_id,
            "kind": "obsidian_note",
            "file": rel_path,
            "vault": vault_name,
            "title": title,
            "tags": tags,
            "aliases": aliases,
            "embedding_text": (
                f"{title} | tags: {', '.join(tags[:10])} | {body[:200]}"
            ),
            "source_type": "obsidian_vault",
            "importance_score": 0.5,
        }

        # ── Index: Block records ───────────────────────────────────────────
        blocks = chunk_by_headings(
            body,
            headings,
            chunk_level=self.heading_chunk_level,
            max_block_size_chars=self.max_block_size_chars,
            min_block_size_chars=self.min_block_size_chars,
        )
        block_index: list[dict] = []
        for idx, block in enumerate(blocks):
            heading_path = block["heading_path"]
            content = block["content"]
            block_id = f"obs_block:{sha1_text(f'{vault_name}:{rel_path}:{heading_path}:{idx}')}"
            block_index.append({
                "id": block_id,
                "kind": "obsidian_block",
                "file": rel_path,
                "vault": vault_name,
                "heading_path": heading_path,
                "content": content,
                "embedding_text": f"{title} > {heading_path} | {content[:400]}",
                "parent_id": note_id,
                "source_type": "obsidian_vault",
                "importance_score": 0.7,
            })

        index_records = [note_index] + block_index

        # ── Detail: Note detail record ─────────────────────────────────────
        detail_records = [
            {
                "id": note_id,
                "kind": "obsidian_note_detail",
                "file": rel_path,
                "vault": vault_name,
                "title": title,
                "aliases": aliases,
                "tags": tags,
                "frontmatter": frontmatter,
                "link_count": len(wikilinks),
                "heading_count": len(headings),
                "tag_count": len(tags),
                "full_content": body[:8000],
                "dataview_queries": [
                    b["raw_query"]
                    for b in dataview_blocks
                    if b["query_type"] == "dql"
                ],
                "source_type": "obsidian_vault",
            }
        ]

        # ── Relations ──────────────────────────────────────────────────────
        relation_records: list[dict] = []

        # Wiki-link relations
        for link in wikilinks:
            resolution = resolve_link(link, known_note_titles, vault_name)
            if resolution["resolved"]:
                relation_records.append({
                    "from": note_id,
                    "to": resolution["target_id"],
                    "type": "obs_wikilink",
                    "resolved": True,
                    "confidence": resolution["confidence"],
                })
            else:
                relation_records.append({
                    "from": note_id,
                    "to": resolution["target_id"],
                    "type": "obs_wikilink_unresolved",
                    "resolved": False,
                    "confidence": 0.0,
                    "heuristic": True,
                })

        # Tag relations
        for tag in tags:
            relation_records.append({
                "from": note_id,
                "to": f"obs_tag:{vault_name}:{tag}",
                "type": "obs_has_tag",
            })

        # ── Graph nodes (returned in stats) ───────────────────────────────
        graph_nodes = [
            {
                "id": note_id,
                "kind": "obsidian_note",
                "file": rel_path,
                "vault": vault_name,
                "title": title,
            }
        ]

        stats: dict[str, Any] = {
            "heading_count": len(headings),
            "link_count": len(wikilinks),
            "tag_count": len(tags),
            "block_count": len(blocks),
            "skipped_links": skipped_links,
            "dataview_block_count": len(dataview_blocks),
            "graph_nodes": graph_nodes,
            "graph_edges": [],
        }

        return index_records, detail_records, relation_records, stats
