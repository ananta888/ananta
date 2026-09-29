"""Chunk layers: line ranges, symbols, stable ids and delta == full-build equivalence."""

from __future__ import annotations

import hashlib

import pytest

from worker.incremental_index import chunking
from worker.incremental_index.chunk_builder import ChunkLayerBuilder, chunk_records
from worker.incremental_index.chunking import chunk_file
from worker.incremental_index.effective_view import overlay_records
from worker.incremental_index.snapshot_diff import FileChange, diff_snapshots

pytestmark = pytest.mark.timeout(60)

MODULE = '''"""Demo module."""

import os


@decorated
def first(a):
    return a


class Small:
    def method(self):
        return 1


def last():
    return os.sep
'''


def covered_lines(chunks):
    return sorted(line for chunk in chunks for line in range(chunk.start_line, chunk.end_line + 1))


# --- chunking -------------------------------------------------------------------------


def test_python_is_chunked_by_top_level_symbol_with_decorators_and_line_ranges():
    chunks = chunk_file("pkg/mod.py", MODULE)
    by_symbol = {chunk.symbol: chunk for chunk in chunks if chunk.symbol}
    assert (by_symbol["first"].start_line, by_symbol["first"].end_line) == (6, 8)
    assert by_symbol["first"].content.startswith("@decorated")
    assert by_symbol["Small"].kind == "class" and by_symbol["last"].kind == "function"
    assert chunks[0].kind == "module" and chunks[0].start_line == 1
    non_blank = [number for number, line in enumerate(MODULE.splitlines(), 1) if line.strip()]
    assert set(non_blank) <= set(covered_lines(chunks))
    assert len(covered_lines(chunks)) == len(set(covered_lines(chunks)))  # no line twice


def test_a_large_class_is_split_by_method(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CLASS_LINES", 5)
    chunks = chunk_file("big.py", "class Big:\n    x = 1\n\n    def a(self):\n        return 1\n\n    def b(self):\n"
                                  "        return 2\n")
    symbols = [(chunk.symbol, chunk.kind) for chunk in chunks]
    assert ("Big.a", "method") in symbols and ("Big.b", "method") in symbols and ("Big", "class") in symbols


def test_invalid_python_and_unknown_files_fall_back_to_line_windows():
    assert [chunk.kind for chunk in chunk_file("broken.py", "def (:\n  pass\n")] == ["python"]
    assert [chunk.kind for chunk in chunk_file("data.json", '{"a": 1}\n')] == ["text"]


def test_markdown_is_chunked_by_heading():
    chunks = chunk_file("doc.md", "intro\n# One\ntext\n## Two\nmore\n")
    assert [(chunk.symbol, chunk.start_line, chunk.end_line) for chunk in chunks] == [
        ("", 1, 1), ("One", 2, 3), ("Two", 4, 5),
    ]


def test_a_huge_file_is_windowed_completely_instead_of_skipped():
    text = "\n".join("line %d with some text" % n for n in range(1, 20001))
    chunks = chunk_file("huge.txt", text)
    assert covered_lines(chunks) == list(range(1, 20001))
    assert all(chunk.end_line - chunk.start_line < chunking.MAX_WINDOW_LINES for chunk in chunks)
    assert all(len(chunk.content) <= chunking.MAX_WINDOW_CHARS for chunk in chunks)


def test_an_oversized_function_is_windowed_but_keeps_its_name():
    body = "\n".join("    x%d = %d" % (n, n) for n in range(600))
    chunks = chunk_file("long.py", "def huge():\n" + body + "\n")
    assert len(chunks) > 1 and {chunk.symbol for chunk in chunks} == {"huge"}


# --- records and ids --------------------------------------------------------------------


def test_symbol_ids_survive_an_edit_above_them():
    before = {row["symbol"]: row for row in chunk_records("pkg/mod.py", MODULE)}
    edited = MODULE.replace("return a", "a = a + 1\n    a = a * 2\n    return a")
    after = {row["symbol"]: row for row in chunk_records("pkg/mod.py", edited)}
    assert after["last"]["id"] == before["last"]["id"]
    assert after["last"]["start_line"] == before["last"]["start_line"] + 2
    assert after["first"]["id"] == before["first"]["id"]
    assert after["first"]["content_hash"] != before["first"]["content_hash"]


def test_records_carry_lines_symbol_and_hashes_but_no_revision():
    row = next(row for row in chunk_records("pkg/mod.py", MODULE) if row["symbol"] == "first")
    assert row["path"] == "pkg/mod.py" and (row["start_line"], row["end_line"]) == (6, 8)
    assert row["file_content_sha256"] == hashlib.sha256(MODULE.encode()).hexdigest()
    assert "revision" not in row and "snapshot_revision" not in row


def test_modify_tombstones_only_ids_that_disappear():
    prior = [row["id"] for row in chunk_records("m.py", MODULE)]
    edited = MODULE.replace("class Small:\n    def method(self):\n        return 1\n", "")
    rows = ChunkLayerBuilder().records([FileChange("modify", "m.py")], {"m.py": edited}, {"m.py": prior})
    tombstones = [row for row in rows if row["tombstone"]]
    small = next(row["id"] for row in chunk_records("m.py", MODULE) if row["symbol"] == "Small")
    assert small in {row["id"] for row in tombstones}
    assert all(row["id"] not in {r["id"] for r in rows if not r["tombstone"]} for row in tombstones)


def test_delete_and_rename_remove_the_old_path():
    prior = {"old.py": ["x1", "x2"], "gone.py": ["g1"]}
    rows = ChunkLayerBuilder().records(
        [FileChange("rename", "old.py", new_path="new.py"), FileChange("delete", "gone.py")],
        {"new.py": "def f():\n    return 1\n"},
        prior,
    )
    tombstoned = {row["id"] for row in rows if row["tombstone"]}
    assert tombstoned == {"x1", "x2", "g1"}
    assert {row["path"] for row in rows if not row["tombstone"]} == {"new.py"}


def test_missing_content_for_a_changed_file_is_an_error():
    with pytest.raises(ValueError, match="chunk_content_missing:a.py"):
        ChunkLayerBuilder().records([FileChange("add", "a.py")], {})


# --- equivalence: full build == base + deltas ---------------------------------------------


SNAPSHOTS = {
    "A": {"a.py": MODULE, "b.md": "# B\ntext\n", "c.txt": "keep\n", "d.py": "def d():\n    return 4\n"},
    "B": {"a.py": MODULE.replace("return 1", "return 2"), "b.md": "# B\ntext\n## New\nx\n", "c.txt": "keep\n",
          "moved.py": "def d():\n    return 4\n", "e.py": "E = 5\n"},
    "C": {"a.py": MODULE.replace("return 1", "return 2"), "c.txt": "keep\n", "moved.py": "def d():\n    return 4\n",
          "e.py": "E = 6\n" + "\n".join("row %d" % n for n in range(400))},
}


def manifest(name):
    files = [{"path": path, "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "byte_size": len(text), "outcome": "indexed"} for path, text in SNAPSHOTS[name].items()]
    return {"snapshot_revision": hashlib.sha256(name.encode()).hexdigest(), "files": files}


def build(builder, view, old, new, parent):
    diff = diff_snapshots(old, new)
    changes = diff.file_changes
    prior = {}
    for row in view:
        prior.setdefault(row["path"], []).append(row["id"])
    content = {path: text for path, text in SNAPSHOTS[new["name"]].items()}
    return builder.build(changes=changes, content=content, prior_ids_by_path=prior, parent_layer_id=parent,
                         snapshot_revision=new["snapshot_revision"], changeset_id=diff.changeset_id)


def named(name):
    return {**manifest(name), "name": name}


def test_base_plus_deltas_equals_a_full_build():
    builder = ChunkLayerBuilder()
    empty = {"snapshot_revision": "0" * 64, "files": [], "name": "empty"}
    base = build(builder, [], empty, named("A"), None)
    view = overlay_records(base["records"])
    delta_b = build(builder, view, named("A"), named("B"), "base")
    view = overlay_records(base["records"], delta_b["records"])
    delta_c = build(builder, view, named("B"), named("C"), "delta-b")
    layered = overlay_records(base["records"], delta_b["records"], delta_c["records"])

    full = overlay_records(build(builder, [], empty, named("C"), None)["records"])
    assert layered == full
    assert {row["path"] for row in layered} == set(SNAPSHOTS["C"])
    assert base["layer_kind"] == "base" and delta_b["layer_kind"] == "delta"
    assert delta_b["tombstone_count"] > 0 and len(delta_c["content_digest"]) == 64
