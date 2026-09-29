from __future__ import annotations

from pathlib import Path

from agent.services.imap_attachment_service import attachment_metadata, download_attachment_securely


def test_attachment_metadata_flags_dangerous_extensions() -> None:
    meta = attachment_metadata(
        [
            {"filename": "report.txt", "content_type": "text/plain", "size": 5},
            {"filename": "../run.sh", "content_type": "text/x-shellscript", "size": 10},
        ]
    )
    assert meta[0]["dangerous"] is False
    assert meta[1]["filename"] == "run.sh"
    assert meta[1]["dangerous"] is True


def test_attachment_download_sanitizes_name_and_computes_hash(tmp_path: Path) -> None:
    result = download_attachment_securely(
        attachment={"filename": "../unsafe.sh", "content": "echo 1", "content_type": "text/x-shellscript"},
        target_dir=tmp_path / "downloads",
    )
    assert result["filename"] == "unsafe.sh"
    assert result["sha256"]
    assert Path(result["path"]).exists()
