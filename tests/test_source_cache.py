from __future__ import annotations

from pathlib import Path

from agent.sources.source_cache import SourceCache
from agent.sources.source_snapshot_store import SourceSnapshotStore


def test_source_cache_raw_extracted_and_clear(tmp_path: Path) -> None:
    cache = SourceCache(root=tmp_path)
    raw_path = cache.put_raw(source_id="s1", payload="raw-content")
    extracted_path = cache.put_extracted(source_id="s1", payload="extracted-content")
    assert raw_path.exists()
    assert extracted_path.exists()
    assert "raw" in str(raw_path)
    assert "extracted" in str(extracted_path)
    removed = cache.clear_source(source_id="s1")
    assert removed >= 2


def test_snapshot_store_marks_duplicate_for_same_content_hash(tmp_path: Path) -> None:
    store = SourceSnapshotStore(root=tmp_path)
    first = store.build_snapshot(
        source_id="src1",
        descriptor_hash="a" * 64,
        content_payload=[{"x": 1}],
        metadata_payload={"m": 1},
        status="indexed",
    )
    second = store.build_snapshot(
        source_id="src1",
        descriptor_hash="a" * 64,
        content_payload=[{"x": 1}],
        metadata_payload={"m": 2},
        status="indexed",
    )
    saved_first = store.save_snapshot(first)
    saved_second = store.save_snapshot(second)
    assert saved_first["status"] == "indexed"
    assert saved_second["status"] == "duplicate"
    assert saved_second["reason_code"] == "duplicate_content_hash"
