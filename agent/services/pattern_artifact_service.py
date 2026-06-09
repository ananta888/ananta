"""Pattern artifact service (PAT-016).

Records deterministic pattern-render manifests as immutable artifact refs
so downstream gates and audit queries can reference them without re-reading
the generated files.

Design:
- Manifest contains pattern_id, language, catalog_version, plan_hash,
  generated file list with per-file sha256 hashes.
- Idempotent: same plan_hash -> same artifact id; repeated calls do not
  create duplicates (content-addressed via plan_hash prefix).
- No DB writes for now; artifacts are stored as JSON files under
  artifacts/patterns/ (same convention as other goal artifacts).
- The service is intentionally stateless; callers hold the returned
  PatternArtifactRecord and decide where to persist it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class PatternGeneratedFile:
    role: str
    path: str
    sha256: str
    size_bytes: int = 0


@dataclass
class PatternArtifactRecord:
    """Immutable record of a pattern render event."""

    artifact_id: str
    pattern_id: str
    language: str
    catalog_version: str
    plan_hash: str
    template_hash: str
    generated_files: list[PatternGeneratedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pattern_artifact.v1",
            "artifact_id": self.artifact_id,
            "pattern_id": self.pattern_id,
            "language": self.language,
            "catalog_version": self.catalog_version,
            "plan_hash": self.plan_hash,
            "template_hash": self.template_hash,
            "generated_files": [
                {
                    "role": f.role,
                    "path": f.path,
                    "sha256": f.sha256,
                    "size_bytes": f.size_bytes,
                }
                for f in self.generated_files
            ],
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }


class PatternArtifactService:
    """Records pattern render results as content-addressed artifacts.

    The service writes one JSON file per render event under
    ``artifacts_root / patterns / <plan_hash[:12]>.json``.
    """

    def __init__(self, artifacts_root: Optional[Path] = None) -> None:
        self._root = artifacts_root or Path("artifacts") / "patterns"

    # --- public surface -----------------------------------------------

    def record(
        self,
        *,
        pattern_id: str,
        language: str,
        catalog_version: str = "unknown",
        plan_hash: str,
        template_hash: str,
        generated_files: list[dict[str, Any]],
        warnings: list[str] | None = None,
    ) -> PatternArtifactRecord:
        """Create and persist a PatternArtifactRecord.

        Idempotent: a second call with the same ``plan_hash`` returns a
        record with the same ``artifact_id`` without writing a new file
        when the existing file is byte-identical.
        """
        artifact_id = f"pat-{plan_hash[:12]}"
        files = [
            PatternGeneratedFile(
                role=str(f.get("role") or "").strip(),
                path=str(f.get("path") or "").strip(),
                sha256=str(f.get("sha256") or "").strip(),
                size_bytes=int(f.get("size_bytes") or 0),
            )
            for f in (generated_files or [])
            if isinstance(f, dict)
        ]
        record = PatternArtifactRecord(
            artifact_id=artifact_id,
            pattern_id=pattern_id,
            language=language,
            catalog_version=catalog_version,
            plan_hash=plan_hash,
            template_hash=template_hash,
            generated_files=files,
            warnings=list(warnings or []),
        )
        self._persist(record)
        return record

    def get(self, plan_hash: str) -> Optional[PatternArtifactRecord]:
        """Load an existing record by plan_hash prefix; returns None if absent."""
        artifact_id = f"pat-{plan_hash[:12]}"
        path = self._root / f"{artifact_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return self._from_dict(data)
        except Exception:
            return None

    # --- internals ----------------------------------------------------

    def _persist(self, record: PatternArtifactRecord) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{record.artifact_id}.json"
        content = json.dumps(record.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                return
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> PatternArtifactRecord:
        files = [
            PatternGeneratedFile(
                role=str(f.get("role") or ""),
                path=str(f.get("path") or ""),
                sha256=str(f.get("sha256") or ""),
                size_bytes=int(f.get("size_bytes") or 0),
            )
            for f in (data.get("generated_files") or [])
            if isinstance(f, dict)
        ]
        return PatternArtifactRecord(
            artifact_id=str(data.get("artifact_id") or ""),
            pattern_id=str(data.get("pattern_id") or ""),
            language=str(data.get("language") or ""),
            catalog_version=str(data.get("catalog_version") or "unknown"),
            plan_hash=str(data.get("plan_hash") or ""),
            template_hash=str(data.get("template_hash") or ""),
            generated_files=files,
            warnings=list(data.get("warnings") or []),
            created_at=float(data.get("created_at") or 0),
        )


def make_plan_hash(pattern_id: str, language: str, parameters: dict[str, Any]) -> str:
    """Stable sha256 hash of a normalized pattern plan (for idempotent artifact IDs)."""
    payload = json.dumps(
        {"pattern_id": pattern_id, "language": language, "parameters": parameters},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_default_service: Optional[PatternArtifactService] = None


def get_pattern_artifact_service(
    artifacts_root: Optional[Path] = None,
) -> PatternArtifactService:
    global _default_service
    if artifacts_root is not None:
        return PatternArtifactService(artifacts_root=artifacts_root)
    if _default_service is None:
        _default_service = PatternArtifactService()
    return _default_service
