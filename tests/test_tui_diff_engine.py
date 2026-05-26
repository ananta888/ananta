from __future__ import annotations

from client_surfaces.operator_tui.diff.diff_engine import DiffEngine


def test_diff_engine_builds_unified_document_from_text_pair() -> None:
    engine = DiffEngine()
    doc = engine.build_document(
        left={"content_type": "text", "text": "a\nb\n", "path": "demo.txt"},
        right={"content_type": "text", "text": "a\nc\n", "path": "demo.txt"},
        render_mode="unified",
    )
    assert doc["schema"] == "diff_document.v1"
    assert doc["stats"]["files"] == 1
    assert doc["stats"]["hunks"] == 1
    hunk = doc["files"][0]["hunks"][0]
    assert "b" in hunk["old_lines"]
    assert "c" in hunk["new_lines"]


def test_diff_engine_builds_side_by_side_document() -> None:
    engine = DiffEngine()
    doc = engine.build_document(
        left={"content_type": "text", "text": "a\nb\n", "path": "demo.txt"},
        right={"content_type": "text", "text": "a\nc\n", "path": "demo.txt"},
        render_mode="side_by_side",
    )
    rows = doc["files"][0]["hunks"][0]["rows"]
    assert any(row["status"] == "removed" for row in rows)
    assert any(row["status"] == "added" for row in rows)


def test_diff_engine_marks_binary_as_unsupported() -> None:
    engine = DiffEngine()
    doc = engine.build_document(
        left={"content_type": "text", "text": "a\x00b", "path": "demo.bin"},
        right={"content_type": "text", "text": "a\x00c", "path": "demo.bin"},
    )
    file_item = doc["files"][0]
    assert file_item["binary"] is True
    assert file_item["unsupported"] is True


def test_diff_engine_marks_truncation_for_large_diff() -> None:
    engine = DiffEngine()
    left_lines = "\n".join(f"a{i}" for i in range(200))
    right_lines = "\n".join(f"b{i}" for i in range(200))
    doc = engine.build_document(
        left={"content_type": "text", "text": left_lines, "path": "big.txt"},
        right={"content_type": "text", "text": right_lines, "path": "big.txt"},
        max_lines=20,
    )
    assert doc["stats"]["truncated"] is True


def test_diff_engine_parses_patch_input() -> None:
    engine = DiffEngine()
    patch = "\n".join(
        [
            "diff --git a/demo.txt b/demo.txt",
            "--- a/demo.txt",
            "+++ b/demo.txt",
            "@@ -1,1 +1,1 @@",
            "-before",
            "+after",
        ]
    )
    doc = engine.build_document(left={"content_type": "patch", "patch": patch})
    assert doc["stats"]["files"] == 1
    assert doc["files"][0]["path"] == "demo.txt"
    assert doc["files"][0]["hunks"][0]["old_lines"] == ["before"]
    assert doc["files"][0]["hunks"][0]["new_lines"] == ["after"]

