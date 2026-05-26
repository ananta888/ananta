from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


_DANGEROUS_EXTENSIONS = {".exe", ".bat", ".cmd", ".sh", ".ps1", ".jar", ".js", ".vbs", ".scr", ".msi"}


def _safe_filename(name: str) -> str:
    candidate = str(name or "").strip() or "attachment.bin"
    candidate = candidate.replace("\\", "/").split("/")[-1]
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate or "attachment.bin"


def attachment_metadata(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(attachments or []):
        if not isinstance(item, dict):
            continue
        name = _safe_filename(str(item.get("filename") or "attachment.bin"))
        content_type = str(item.get("content_type") or "application/octet-stream")
        size = int(item.get("size") or len(str(item.get("content") or "").encode("utf-8")))
        ext = Path(name).suffix.lower()
        rows.append(
            {
                "filename": name,
                "content_type": content_type,
                "size": size,
                "dangerous": ext in _DANGEROUS_EXTENSIONS,
                "danger_reason_code": "dangerous_extension" if ext in _DANGEROUS_EXTENSIONS else "clean",
            }
        )
    return rows


def download_attachment_securely(
    *,
    attachment: dict[str, Any],
    target_dir: str | Path,
) -> dict[str, Any]:
    item = dict(attachment or {})
    name = _safe_filename(str(item.get("filename") or "attachment.bin"))
    content = item.get("content")
    if isinstance(content, bytes):
        blob = content
    else:
        blob = str(content or "").encode("utf-8")
    destination_root = Path(target_dir).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    path = (destination_root / name).resolve()
    if destination_root not in path.parents and path != destination_root:
        raise ValueError("attachment_path_traversal_detected")
    path.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    ext = path.suffix.lower()
    return {
        "filename": name,
        "path": str(path),
        "size": len(blob),
        "sha256": digest,
        "dangerous": ext in _DANGEROUS_EXTENSIONS,
        "danger_reason_code": "dangerous_extension" if ext in _DANGEROUS_EXTENSIONS else "clean",
    }
