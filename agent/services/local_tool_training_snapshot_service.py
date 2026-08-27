"""Immutable, time-windowed snapshots for governed tool-learning records."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from agent.services.local_tool_training_redaction import (
    LocalToolTrainingRedactionPolicy,
    ToolTrainingRedactionError,
)
from agent.services.ml_intern_provenance_contract import normalize_run_ids, normalize_source_ids
from ananta_contracts.local_tool_training import (
    ToolInteractionTrainingRecord,
    ToolTrainingDatasetSnapshot,
)


class ToolTrainingSnapshotError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class LocalToolTrainingSnapshotService:
    """Partitions by event time and rejects similarity leakage across holdouts."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        allowed_source_ids: Iterable[str],
        allowed_run_ids: Iterable[str],
        collector_policy_sha256: str,
        redaction: LocalToolTrainingRedactionPolicy | None = None,
    ) -> None:
        root = Path(storage_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ToolTrainingSnapshotError("snapshot_storage_root_unsafe")
        self._root = root.resolve(strict=True)
        self._root.chmod(0o700)
        self._allowed_sources = frozenset(normalize_source_ids(tuple(allowed_source_ids)))
        self._allowed_runs = frozenset(normalize_run_ids(tuple(allowed_run_ids)))
        self._collector_policy_sha256 = _digest(collector_policy_sha256)
        self._redaction = redaction or LocalToolTrainingRedactionPolicy()

    def create(
        self,
        *,
        dataset_id: str,
        records: Iterable[ToolInteractionTrainingRecord],
        train_end: str,
        validation_end: str,
        test_end: str,
        source_ids: Iterable[str],
        run_ids: Iterable[str],
        collector_policy_sha256: str,
        redaction_policy_sha256: str,
        created_at: str,
    ) -> ToolTrainingDatasetSnapshot:
        sources = normalize_source_ids(tuple(source_ids))
        runs = normalize_run_ids(tuple(run_ids))
        if not sources or not runs:
            raise ToolTrainingSnapshotError("provenance_unverified")
        if not set(sources).issubset(self._allowed_sources) or not set(runs).issubset(self._allowed_runs):
            raise ToolTrainingSnapshotError("provenance_unverified")
        boundaries = tuple(_parse_time(value) for value in (train_end, validation_end, test_end))
        if not boundaries[0] < boundaries[1] < boundaries[2]:
            raise ToolTrainingSnapshotError("snapshot_time_windows_invalid")
        if _digest(collector_policy_sha256) != self._collector_policy_sha256:
            raise ToolTrainingSnapshotError("snapshot_collector_policy_mismatch")
        if _digest(redaction_policy_sha256) != self._redaction.digest:
            raise ToolTrainingSnapshotError("snapshot_redaction_policy_mismatch")
        partitions: dict[str, list[ToolInteractionTrainingRecord]] = {
            "train": [],
            "validation": [],
            "test": [],
        }
        group_partitions: dict[str, set[str]] = {}
        seen_ids: set[str] = set()
        for record in records:
            if record.interaction_id in seen_ids:
                raise ToolTrainingSnapshotError("snapshot_interaction_duplicate")
            seen_ids.add(record.interaction_id)
            if record.redaction_policy_sha256 != redaction_policy_sha256:
                raise ToolTrainingSnapshotError("snapshot_redaction_policy_mismatch")
            if record.collector_policy_sha256 != collector_policy_sha256:
                raise ToolTrainingSnapshotError("snapshot_collector_policy_mismatch")
            try:
                candidate_arguments = self._redaction.sanitize_arguments(record.candidate.arguments)
                outcome_arguments = self._redaction.sanitize_arguments(record.independent_outcome.arguments)
            except ToolTrainingRedactionError as exc:
                raise ToolTrainingSnapshotError("snapshot_record_redaction_failed") from exc
            if (
                candidate_arguments != record.candidate.arguments
                or outcome_arguments != record.independent_outcome.arguments
            ):
                raise ToolTrainingSnapshotError("snapshot_record_redaction_failed")
            observed = _parse_time(record.observed_at)
            if observed <= boundaries[0]:
                partition = "train"
            elif observed <= boundaries[1]:
                partition = "validation"
            elif observed <= boundaries[2]:
                partition = "test"
            else:
                raise ToolTrainingSnapshotError("snapshot_record_outside_window")
            partitions[partition].append(record)
            group_partitions.setdefault(record.similarity_group_sha256, set()).add(partition)
        if any(len(values) > 1 for values in group_partitions.values()):
            raise ToolTrainingSnapshotError("snapshot_similarity_leakage")
        if any(not values for values in partitions.values()):
            raise ToolTrainingSnapshotError("snapshot_partition_empty")

        encoded = {name: _jsonl(values) for name, values in partitions.items()}
        digests = {name: hashlib.sha256(payload).hexdigest() for name, payload in encoded.items()}
        normalized_created_at = _parse_time(created_at)
        if normalized_created_at < boundaries[2]:
            raise ToolTrainingSnapshotError("snapshot_created_before_window_closed")
        core = {
            "schema_version": "ananta.local-tool-training-snapshot.v1",
            "dataset_id": dataset_id,
            "created_at": _format_time(normalized_created_at),
            "train_end": _format_time(boundaries[0]),
            "validation_end": _format_time(boundaries[1]),
            "test_end": _format_time(boundaries[2]),
            "source_ids": list(sources),
            "run_ids": list(runs),
            "collector_policy_sha256": _digest(collector_policy_sha256),
            "redaction_policy_sha256": _digest(redaction_policy_sha256),
            "train_sha256": digests["train"],
            "validation_sha256": digests["validation"],
            "test_sha256": digests["test"],
            "train_records": len(partitions["train"]),
            "validation_records": len(partitions["validation"]),
            "test_records": len(partitions["test"]),
            "verification_status": "verified",
        }
        manifest_sha = hashlib.sha256(_canonical(core)).hexdigest()
        snapshot = ToolTrainingDatasetSnapshot(
            snapshot_id=f"snap-{manifest_sha[:32]}",
            manifest_sha256=manifest_sha,
            **core,
        )
        self._persist(snapshot, encoded)
        return snapshot

    def _persist(self, snapshot: ToolTrainingDatasetSnapshot, encoded: dict[str, bytes]) -> None:
        destination = self._root / snapshot.snapshot_id
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ToolTrainingSnapshotError("snapshot_immutable_conflict")
            manifest = destination / "manifest.json"
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if existing == snapshot.to_wire() and all(
                hashlib.sha256((destination / f"{name}.jsonl").read_bytes()).hexdigest()
                == getattr(snapshot, f"{name}_sha256")
                for name in ("train", "validation", "test")
            ):
                return
            raise ToolTrainingSnapshotError("snapshot_immutable_conflict")
        temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self._root))
        try:
            for name, payload in encoded.items():
                path = temporary / f"{name}.jsonl"
                path.write_bytes(payload)
                path.chmod(0o400)
            manifest = temporary / "manifest.json"
            manifest.write_bytes(_canonical(snapshot.to_wire()) + b"\n")
            manifest.chmod(0o400)
            temporary.chmod(0o500)
            os.replace(temporary, destination)
        except Exception:
            temporary.chmod(0o700)
            for path in temporary.glob("*"):
                path.chmod(0o600)
                path.unlink(missing_ok=True)
            temporary.rmdir()
            raise


def _jsonl(records: Iterable[ToolInteractionTrainingRecord]) -> bytes:
    ordered = sorted(records, key=lambda item: (item.observed_at, item.interaction_id))
    return b"".join(_canonical(item.to_wire()) + b"\n" for item in ordered)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolTrainingSnapshotError("snapshot_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ToolTrainingSnapshotError("snapshot_time_invalid")
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ToolTrainingSnapshotError("snapshot_policy_digest_invalid")
    return normalized


__all__ = ["LocalToolTrainingSnapshotService", "ToolTrainingSnapshotError"]
