from __future__ import annotations

from typing import Any, Mapping


def build_open_notebook_provenance(metadata: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(metadata or {})
    license_ref = str(payload.get("license") or payload.get("license_ref") or "").strip()
    return {
        "source_system": "open_notebook",
        "original_url": str(payload.get("url") or payload.get("canonical_url") or "").strip() or None,
        "original_file_path": str(payload.get("file_path") or "").strip() or None,
        "imported_at": str(payload.get("imported_at") or "").strip() or None,
        "export_version": str(payload.get("export_version") or "").strip() or None,
        "license_ref": license_ref or None,
        "license_status": "known" if license_ref and license_ref.lower() != "unknown" else "unknown",
    }
