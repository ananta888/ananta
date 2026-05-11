from __future__ import annotations

import struct
from pathlib import Path

import pytest

from agent.services.wiki_zim_parser import ZimParser, _strip_html

# ---------------------------------------------------------------------------
# Synthetic ZIM file builder
# ---------------------------------------------------------------------------

_ZIM_MAGIC = 0x044D495A
_HEADER = struct.Struct("<IHH16sIIQQQQIIQ")  # 80 bytes
_MIME_REDIRECT = 0xFFFF
_MIME_DELETED = 0xFFFE


def _build_uncompressed_cluster(blobs: list[bytes]) -> bytes:
    """Build a ZIM uncompressed cluster (compression type 0)."""
    n = len(blobs)
    offset_table_size = (n + 1) * 4
    offsets: list[int] = []
    cursor = offset_table_size
    for blob in blobs:
        offsets.append(cursor)
        cursor += len(blob)
    offsets.append(cursor)
    data = struct.pack(f"<{n + 1}I", *offsets)
    for blob in blobs:
        data += blob
    return bytes([0]) + data  # compression byte 0 = uncompressed


def _build_zim(
    articles: list[tuple[str, str, str]],  # (namespace, title, html_content)
    redirects: list[tuple[str, str, int]] | None = None,  # (namespace, title, target_idx)
    deleted: list[tuple[str, str]] | None = None,  # (namespace, title)
    mime_type: str = "text/html",
    cluster_compress: int = 0,
) -> bytes:
    """
    Build a minimal ZIM v5 file for testing.

    All articles go into one cluster (uncompressed by default).
    Redirects and deleted entries are appended after articles in the URL list.
    """
    redirects = redirects or []
    deleted = deleted or []

    # ----- MIME type list -----
    mime_bytes = mime_type.encode("utf-8") + b"\x00\x00"

    # ----- Positions -----
    mime_list_pos = _HEADER.size  # 80
    url_ptr_pos = mime_list_pos + len(mime_bytes)
    n_entries = len(articles) + len(redirects) + len(deleted)
    url_ptr_pos_end = url_ptr_pos + n_entries * 8
    title_ptr_pos = url_ptr_pos_end
    title_ptr_pos_end = title_ptr_pos + n_entries * 4
    entries_start = title_ptr_pos_end

    # ----- Build directory entries -----
    entry_bytes_list: list[bytes] = []

    for i, (ns, title, _) in enumerate(articles):
        url = f"{ns}/{title.replace(' ', '_')}"
        base = struct.pack("<HBBI", 0, 0, ord(ns), 0)
        extra = struct.pack("<II", 0, i)  # cluster 0, blob i
        entry = base + extra + url.encode("utf-8") + b"\x00" + title.encode("utf-8") + b"\x00"
        entry_bytes_list.append(entry)

    for ns, title, target_idx in redirects:
        url = f"{ns}/{title.replace(' ', '_')}"
        base = struct.pack("<HBBI", _MIME_REDIRECT, 0, ord(ns), 0)
        extra = struct.pack("<I", target_idx)
        entry = base + extra + url.encode("utf-8") + b"\x00" + title.encode("utf-8") + b"\x00"
        entry_bytes_list.append(entry)

    for ns, title in deleted:
        url = f"{ns}/{title.replace(' ', '_')}"
        base = struct.pack("<HBBI", _MIME_DELETED, 0, ord(ns), 0)
        # deleted has same layout as redirect for the extra field
        extra = struct.pack("<I", 0)
        entry = base + extra + url.encode("utf-8") + b"\x00" + title.encode("utf-8") + b"\x00"
        entry_bytes_list.append(entry)

    # Compute entry offsets
    entry_offsets: list[int] = []
    pos = entries_start
    for eb in entry_bytes_list:
        entry_offsets.append(pos)
        pos += len(eb)
    entries_end = pos

    # ----- Cluster pointer list -----
    cluster_ptr_pos = entries_end
    cluster_start = cluster_ptr_pos + 8  # one cluster

    # ----- Cluster data -----
    if cluster_compress == 0:
        blobs = [content.encode("utf-8") for _, _, content in articles]
        cluster_data = _build_uncompressed_cluster(blobs)
    elif cluster_compress == 5:  # LZ4 marker only (invalid data, just tests issue reporting)
        cluster_data = bytes([5]) + b"\x00" * 8
    else:
        cluster_data = bytes([cluster_compress]) + b"\x00" * 8

    checksum_pos = cluster_start + len(cluster_data)

    # ----- Title pointer list (sorted by title, articles only) -----
    all_titles = [t for _, t, _ in articles] + [t for _, t, _ in redirects] + [t for _, t in deleted]
    sorted_indices = sorted(range(n_entries), key=lambda i: all_titles[i].lower())
    title_ptr_bytes = struct.pack(f"<{n_entries}I", *sorted_indices) if n_entries else b""

    # ----- Assemble -----
    header = _HEADER.pack(
        _ZIM_MAGIC, 5, 0, bytes(16),
        n_entries, 1,
        url_ptr_pos, title_ptr_pos, cluster_ptr_pos, mime_list_pos,
        0xFFFFFFFF, 0xFFFFFFFF,
        checksum_pos,
    )

    result = header
    result += mime_bytes
    result += struct.pack(f"<{n_entries}Q", *entry_offsets) if n_entries else b""
    result += title_ptr_bytes
    for eb in entry_bytes_list:
        result += eb
    result += struct.pack("<Q", cluster_start)
    result += cluster_data
    result += bytes(16)  # checksum placeholder
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strip_html_basic():
    assert _strip_html("<p>Hello <b>World</b></p>") == "Hello World"


def test_strip_html_entities():
    assert _strip_html("&lt;tag&gt; &amp; &quot;text&quot;") == '<tag> & "text"'


def test_strip_html_removes_style_and_script():
    html = "<style>body{color:red}</style><p>Text</p><script>alert(1)</script>"
    assert _strip_html(html) == "Text"


def test_strip_html_collapses_whitespace():
    assert _strip_html("  foo  \n  bar  ") == "foo bar"


def test_zim_parser_parses_two_articles(tmp_path: Path):
    zim_bytes = _build_zim([
        ("A", "Python", "<html><body><p>Python is a programming language.</p></body></html>"),
        ("A", "Java", "<html><body><p>Java is another language.</p></body></html>"),
    ])
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(zim_bytes)

    parser = ZimParser()
    items = list(parser.iter_items(corpus_path=zim_file))

    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"Python", "Java"}
    for item in items:
        assert item["kind"] == "page"
        assert item["namespace"] == 0
        assert item["is_redirect"] is False
        assert item["redirect_title"] is None
        assert "language" in item["text"]
    assert parser.issues == []


def test_zim_parser_strips_html(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim([
        ("A", "Article", "<h1>Title</h1><p>Body &amp; more.</p>"),
    ]))

    items = list(ZimParser().iter_items(corpus_path=zim_file))

    assert len(items) == 1
    assert items[0]["text"] == "Title Body & more."


def test_zim_parser_handles_redirect(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim(
        articles=[("A", "Python", "<p>Python language</p>")],
        redirects=[("A", "Py", 0)],  # redirects to article index 0
    ))

    parser = ZimParser()
    items = list(parser.iter_items(corpus_path=zim_file))

    articles = [i for i in items if not i["is_redirect"]]
    redirects = [i for i in items if i["is_redirect"]]
    assert len(articles) == 1
    assert len(redirects) == 1
    assert redirects[0]["title"] == "Py"
    assert redirects[0]["redirect_title"] == "Python"
    assert redirects[0]["text"] == ""
    assert parser.issues == []


def test_zim_parser_skips_deleted_entries(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim(
        articles=[("A", "Python", "<p>Python language</p>")],
        deleted=[("A", "OldArticle")],
    ))

    items = list(ZimParser().iter_items(corpus_path=zim_file))

    titles = [i["title"] for i in items]
    assert "OldArticle" not in titles


def test_zim_parser_non_article_namespace_yields_minus_one(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim([
        ("I", "logo.png", "fake image data"),
        ("A", "RealArticle", "<p>Content</p>"),
    ], mime_type="text/plain"))

    items = list(ZimParser().iter_items(corpus_path=zim_file))

    ns_map = {item["title"]: item["namespace"] for item in items}
    assert ns_map["RealArticle"] == 0
    assert ns_map["logo.png"] == -1


def test_zim_parser_reports_lz4_as_issue(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim(
        articles=[("A", "Article", "<p>text</p>")],
        cluster_compress=5,  # LZ4
    ))

    parser = ZimParser()
    items = list(parser.iter_items(corpus_path=zim_file))

    assert items == []
    assert "lz4_compression_not_supported" in parser.issues


def test_zim_parser_reports_unknown_compression(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim(
        articles=[("A", "Article", "<p>text</p>")],
        cluster_compress=9,
    ))

    parser = ZimParser()
    items = list(parser.iter_items(corpus_path=zim_file))

    assert items == []
    assert any("unknown_compression_type" in issue for issue in parser.issues)


def test_zim_parser_raises_on_wrong_magic(tmp_path: Path):
    bad_file = tmp_path / "bad.zim"
    bad_file.write_bytes(b"\x00" * 80)

    with pytest.raises(ValueError, match="not_a_zim_file"):
        list(ZimParser().iter_items(corpus_path=bad_file))


def test_zim_parser_raises_on_truncated_file(tmp_path: Path):
    small_file = tmp_path / "small.zim"
    small_file.write_bytes(b"\x00" * 10)

    with pytest.raises(ValueError, match="zim_file_too_small"):
        list(ZimParser().iter_items(corpus_path=small_file))


def test_zim_parser_index_path_reported_as_issue(tmp_path: Path):
    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim([("A", "Article", "<p>text</p>")]))

    parser = ZimParser()
    list(parser.iter_items(corpus_path=zim_file, index_path=tmp_path / "unused.idx"))

    assert any("zim_index_path_ignored" in issue for issue in parser.issues)


def test_zim_parser_compatible_with_wiki_normalizer(tmp_path: Path):
    """ZimParser output feeds cleanly into WikiRecordNormalizer."""
    from agent.services.wiki_normalizer import WikiRecordNormalizer

    zim_file = tmp_path / "test.zim"
    zim_file.write_bytes(_build_zim([
        ("A", "Ananta", "<p>Ananta is a multi-agent task orchestration system built for developers.</p>"),
        ("A", "Redirect_target", ""),  # empty content → filtered by normalizer
    ]))

    parser = ZimParser()
    normalizer = WikiRecordNormalizer()
    records: list[dict] = []
    errors: list[dict] = []
    for ordinal, item in enumerate(parser.iter_items(corpus_path=zim_file), start=1):
        chunks, err = normalizer.normalize_item(
            item=item,
            source_path=zim_file,
            source_id="test-zim",
            ordinal=ordinal,
            default_language="en",
            source_format="zim",
        )
        records.extend(chunks)
        if err:
            errors.append(err)

    assert any(r["article_title"] == "Ananta" for r in records)
    assert all(r["import_metadata"]["format"] == "zim" for r in records)
    assert all(r["kind"] == "wiki_section_chunk" for r in records)


def test_zim_parser_issues_reset_between_calls(tmp_path: Path):
    bad_file = tmp_path / "bad.zim"
    bad_file.write_bytes(b"\x00" * 10)

    good_file = tmp_path / "good.zim"
    good_file.write_bytes(_build_zim([("A", "Article", "<p>text</p>")]))

    parser = ZimParser()
    with pytest.raises(ValueError):
        list(parser.iter_items(corpus_path=bad_file))

    items = list(parser.iter_items(corpus_path=good_file))
    assert len(items) == 1
    assert parser.issues == []
