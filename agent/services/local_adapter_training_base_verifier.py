"""Filesystem verification for catalog-owned local-adapter training bases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ananta_contracts.local_adapter_training_base import LocalAdapterTrainingBasePin


@dataclass(frozen=True, slots=True)
class TrainingBaseVerification:
    passed: bool
    catalog_id: str
    verified_artifacts: int
    reason_codes: tuple[str, ...]


class LocalAdapterTrainingBaseVerifier:
    """Verify immutable artifact metadata without loading executable content."""

    def verify(self, base: LocalAdapterTrainingBasePin, root: Path) -> TrainingBaseVerification:
        reasons: list[str] = []
        verified = 0
        try:
            resolved_root = root.resolve(strict=True)
        except FileNotFoundError:
            return TrainingBaseVerification(
                passed=False,
                catalog_id=base.catalog_id,
                verified_artifacts=0,
                reason_codes=("training_base_root_missing",),
            )
        if not resolved_root.is_dir():
            return TrainingBaseVerification(
                passed=False,
                catalog_id=base.catalog_id,
                verified_artifacts=0,
                reason_codes=("training_base_root_not_directory",),
            )
        for artifact in base.artifacts:
            candidate = (resolved_root / artifact.relative_path).resolve(strict=False)
            if not candidate.is_relative_to(resolved_root):
                reasons.append(f"artifact_path_escape:{artifact.relative_path}")
                continue
            if not candidate.is_file():
                reasons.append(f"artifact_missing:{artifact.relative_path}")
                continue
            if candidate.stat().st_size != artifact.size_bytes:
                reasons.append(f"artifact_size_mismatch:{artifact.relative_path}")
                continue
            if _file_sha256(candidate) != artifact.sha256:
                reasons.append(f"artifact_sha256_mismatch:{artifact.relative_path}")
                continue
            verified += 1
        return TrainingBaseVerification(
            passed=not reasons,
            catalog_id=base.catalog_id,
            verified_artifacts=verified,
            reason_codes=tuple(reasons) or ("local_adapter_training_base_verified",),
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["LocalAdapterTrainingBaseVerifier", "TrainingBaseVerification"]
