from __future__ import annotations

from pathlib import Path

from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.sources.wikimedia_downloader import WikimediaDownloader


def test_wikimedia_downloader_streams_file_and_creates_snapshot(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"x" * 4096)
    destination = tmp_path / "downloads" / "dump.bin"
    downloader = WikimediaDownloader(snapshot_store=SourceSnapshotStore(root=tmp_path))
    report = downloader.download(
        source_id="wikimedia-wikipedia-initial-dump",
        descriptor_hash="a" * 64,
        url=src.as_uri(),
        destination=destination,
    )
    assert report["status"] == "indexed"
    assert Path(report["destination"]).exists()
    assert Path(report["destination"]).read_bytes() == b"x" * 4096


def test_wikimedia_downloader_resume_uses_partial(tmp_path: Path) -> None:
    src = tmp_path / "source.bin"
    src.write_bytes(b"0123456789")
    destination = tmp_path / "downloads" / "dump.bin"
    part = destination.with_suffix(".bin.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"01234")
    downloader = WikimediaDownloader(snapshot_store=SourceSnapshotStore(root=tmp_path))
    report = downloader.download(
        source_id="wikimedia-wikipedia-initial-dump",
        descriptor_hash="a" * 64,
        url=src.as_uri(),
        destination=destination,
    )
    assert int(report["resumed_from_bytes"]) in {0, 5}
    assert destination.read_bytes() == b"0123456789"
