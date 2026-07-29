"""Dataset admission and leakage checks performed before loading a model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from worker.training.contracts import DatasetManifest, SplitManifest, TrainingContractError, ValidationDatasetManifest


@dataclass(frozen=True)
class VerifiedDataset:
    train_path: Path
    validation_path: Path
    train_records: int
    validation_records: int
    dataset_hash: str


@dataclass(frozen=True)
class VerifiedValidationDataset:
    validation_path: Path
    validation_records: int
    dataset_hash: str


class DatasetValidator:
    """Verify immutable JSONL split manifests and prevent exact leakage."""

    def __init__(
        self,
        root: Path,
        *,
        max_split_bytes: int = 4 * 1024**3,
        max_records: int = 10_000_000,
        max_record_bytes: int = 4 * 1024**2,
    ) -> None:
        self._root = root.resolve()
        self._max_split_bytes = max_split_bytes
        self._max_records = max_records
        self._max_record_bytes = max_record_bytes

    def validate(self, manifest: DatasetManifest) -> VerifiedDataset:
        train_path, train_hashes = self._validate_split(manifest.train, "train")
        validation_path, validation_hashes = self._validate_split(manifest.validation, "validation")
        overlap = train_hashes.intersection(validation_hashes)
        if overlap:
            raise TrainingContractError(
                "cross_split_leakage",
                f"train and validation contain {len(overlap)} identical record(s)",
            )
        return VerifiedDataset(
            train_path=train_path,
            validation_path=validation_path,
            train_records=len(train_hashes),
            validation_records=len(validation_hashes),
            dataset_hash=manifest.identity_hash,
        )

    def validate_validation(self, manifest: ValidationDatasetManifest) -> VerifiedValidationDataset:
        validation_path, validation_hashes = self._validate_split(manifest.validation, "validation")
        return VerifiedValidationDataset(
            validation_path=validation_path,
            validation_records=len(validation_hashes),
            dataset_hash=manifest.identity_hash,
        )

    def _validate_split(self, split: SplitManifest, name: str) -> tuple[Path, set[str]]:
        path = self._resolve(split.relative_path)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise TrainingContractError("dataset_missing", f"{name} split does not exist") from exc
        if not path.is_file() or stat.st_size <= 0:
            raise TrainingContractError("dataset_empty", f"{name} split must be a non-empty file")
        if stat.st_size > self._max_split_bytes:
            raise TrainingContractError(
                "dataset_too_large", f"{name} split exceeds the configured byte limit", http_status=413
            )

        raw_hash = hashlib.sha256()
        canonical_hashes: set[str] = set()
        record_count = 0
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                raw_hash.update(raw_line)
                if not raw_line.strip():
                    continue
                if len(raw_line) > self._max_record_bytes:
                    raise TrainingContractError(
                        "record_too_large",
                        f"{name} line {line_number} exceeds the configured byte limit",
                        http_status=413,
                    )
                record = self._decode_record(raw_line, name, line_number)
                record_count += 1
                if record_count > min(self._max_records, split.record_count):
                    raise TrainingContractError("record_count_mismatch", f"{name} record count exceeds its manifest")
                canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if digest in canonical_hashes:
                    raise TrainingContractError("duplicate_record", f"{name} contains a duplicate record")
                canonical_hashes.add(digest)

        if record_count == 0:
            raise TrainingContractError("dataset_empty", f"{name} split contains no records")
        if record_count != split.record_count:
            raise TrainingContractError(
                "record_count_mismatch",
                f"{name} manifest declares {split.record_count} records but file contains {record_count}",
            )
        if raw_hash.hexdigest() != split.sha256:
            raise TrainingContractError("dataset_hash_mismatch", f"{name} split SHA-256 does not match its manifest")
        return path, canonical_hashes

    def _resolve(self, relative_path: str) -> Path:
        unresolved = self._root / relative_path
        current = self._root
        for part in Path(relative_path).parts:
            current = current / part
            if current.is_symlink():
                raise TrainingContractError("invalid_path", "dataset path must not contain symbolic links")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise TrainingContractError("invalid_path", "dataset path escapes the configured dataset root") from exc
        return candidate

    @staticmethod
    def _decode_record(raw_line: bytes, split: str, line_number: int) -> Mapping[str, Any]:
        try:
            record = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingContractError(
                "invalid_dataset_json", f"{split} line {line_number} is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(record, Mapping):
            raise TrainingContractError("invalid_dataset_record", f"{split} line {line_number} must be an object")
        has_messages = isinstance(record.get("messages"), list) and bool(record["messages"])
        has_text = isinstance(record.get("text"), str) and bool(record["text"].strip())
        has_pair = (
            isinstance(record.get("instruction"), str)
            and bool(record["instruction"].strip())
            and isinstance(record.get("output"), str)
            and bool(record["output"].strip())
        )
        embedding_label = record.get("label")
        has_embedding_pair = (
            isinstance(record.get("sentence_A"), str)
            and bool(record["sentence_A"].strip())
            and isinstance(record.get("sentence_B"), str)
            and bool(record["sentence_B"].strip())
            and not isinstance(embedding_label, bool)
            and isinstance(embedding_label, (int, float))
            and 0.0 <= float(embedding_label) <= 1.0
        )
        if not (
            has_messages
            or has_text
            or has_pair
            or has_embedding_pair
        ):
            raise TrainingContractError(
                "invalid_dataset_record",
                (
                    f"{split} line {line_number} requires messages, "
                    "text, instruction/output, or a sentence pair"
                ),
            )
        if has_messages:
            for message in record["messages"]:
                if (
                    not isinstance(message, Mapping)
                    or not str(message.get("role") or "").strip()
                    or not isinstance(message.get("content"), str)
                ):
                    raise TrainingContractError(
                        "invalid_dataset_record",
                        f"{split} line {line_number} contains an invalid message",
                    )
        return record


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield admitted records; callers must pass a path from ``VerifiedDataset``."""

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
