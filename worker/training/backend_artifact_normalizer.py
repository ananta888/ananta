"""Normalize external trainer outputs before the generic runtime admits them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from worker.training.backends.base import TrainingBackendError

ARTIFACT_MANIFEST_VERSION = "ananta.training-backend-artifacts.v1"
_ALLOWED_NAMES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "backend-config.json",
        "evaluation.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)


@dataclass(frozen=True, slots=True)
class NormalizedBackendArtifacts:
    paths: tuple[Path, ...]
    manifest_path: Path
    manifest_sha256: str


class BackendArtifactNormalizer:
    def normalize(
        self,
        *,
        artifact_root: Path,
        candidates: Iterable[Path],
        binding: Mapping[str, str],
    ) -> NormalizedBackendArtifacts:
        root = artifact_root.resolve()
        required_binding = {
            "attempt_id",
            "backend",
            "backend_version",
            "base_model_sha256",
            "configuration_sha256",
            "dataset_sha256",
            "job_id",
        }
        if set(binding) != required_binding or any(
            not isinstance(value, str) or not value for value in binding.values()
        ):
            raise TrainingBackendError("artifact_invalid", "backend artifact binding is incomplete")
        accepted: list[Path] = []
        entries: list[dict[str, Any]] = []
        for raw in candidates:
            if raw.name not in _ALLOWED_NAMES or raw.is_symlink():
                continue
            path = raw.resolve(strict=True)
            try:
                relative = path.relative_to(root)
            except ValueError as exc:
                raise TrainingBackendError("artifact_invalid", "backend artifact escapes its output root") from exc
            if not path.is_file() or path.stat().st_size > 20 * 1024**3:
                raise TrainingBackendError("artifact_invalid", "backend artifact is missing or exceeds its bound")
            digest = _file_sha256(path)
            accepted.append(path)
            entries.append({"name": relative.as_posix(), "sha256": digest, "size_bytes": path.stat().st_size})
        names = {path.name for path in accepted}
        if not {"adapter_config.json", "adapter_model.safetensors"}.issubset(names):
            raise TrainingBackendError("artifact_invalid", "normalized adapter files are incomplete")
        manifest = root / "ananta-backend-manifest.json"
        payload: dict[str, Any] = {
            "schema_version": ARTIFACT_MANIFEST_VERSION,
            **dict(binding),
            "artifacts": sorted(entries, key=lambda item: item["name"]),
        }
        manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return NormalizedBackendArtifacts(
            paths=tuple(sorted({*accepted, manifest}, key=lambda path: str(path))),
            manifest_path=manifest,
            manifest_sha256=_file_sha256(manifest),
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["ARTIFACT_MANIFEST_VERSION", "BackendArtifactNormalizer", "NormalizedBackendArtifacts"]
