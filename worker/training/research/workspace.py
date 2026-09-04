"""Read-only, digest-verifying view of one task-bound research workspace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ananta_contracts.research_training_data import ResearchDatasetManifestV1
from ananta_contracts.research_training_execution import ResearchArtifactInputV1


class ResearchWorkspaceReader:
    def __init__(self, root: str | Path, *, maximum_input_bytes: int) -> None:
        self._root = Path(root).resolve()
        self._maximum = int(maximum_input_bytes)
        if not self._root.is_dir() or not 1 <= self._maximum <= 1 << 50:
            raise ValueError("research_workspace_invalid")

    def read_dataset(self, manifest: ResearchDatasetManifestV1) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
        consumed = 0
        for shard in manifest.shards:
            content = self._read(shard.relative_ref, shard.content_digest, shard.size_bytes)
            consumed += len(content)
            if consumed > self._maximum:
                raise ValueError("research_workspace_input_budget_exceeded")
            result[shard.split].extend(self._records(content, shard.media_type))
        return result

    def read_artifact(self, artifact: ResearchArtifactInputV1) -> bytes:
        return self._read(artifact.relative_ref, artifact.artifact_digest, artifact.size_bytes)

    def _read(self, relative_ref: str, digest: str, expected_size: int | None) -> bytes:
        target = (self._root / relative_ref).resolve()
        if self._root not in target.parents or not target.is_file() or target.is_symlink():
            raise PermissionError("research_workspace_ref_invalid")
        stat_before = target.stat()
        if stat_before.st_size > self._maximum:
            raise ValueError("research_workspace_input_budget_exceeded")
        content = target.read_bytes()
        stat_after = target.stat()
        if (
            stat_before.st_ino != stat_after.st_ino
            or stat_before.st_size != stat_after.st_size
            or stat_before.st_mtime_ns != stat_after.st_mtime_ns
        ):
            raise ValueError("research_workspace_input_changed")
        if expected_size is not None and len(content) != expected_size:
            raise ValueError("research_workspace_input_size_mismatch")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("research_workspace_input_digest_mismatch")
        return content

    @staticmethod
    def _records(content: bytes, media_type: str) -> list[dict[str, Any]]:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("research_dataset_utf8_required") from exc
        if media_type == "text_plain":
            records = [{"text": line} for line in text.splitlines() if line]
        elif media_type == "application_jsonl":
            records = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("research_dataset_jsonl_invalid") from exc
                if not isinstance(value, dict) or set(value) - {
                    "text",
                    "messages",
                    "prompt",
                    "response",
                    "expected",
                }:
                    raise ValueError("research_dataset_record_invalid")
                records.append(value)
        else:
            raise ValueError("research_dataset_media_type_unsupported")
        if not records:
            raise ValueError("research_dataset_shard_empty")
        return records


__all__ = ["ResearchWorkspaceReader"]
