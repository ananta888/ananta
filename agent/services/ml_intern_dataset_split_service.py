"""Deterministic, duplicate-group-safe train/validation splitting."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from agent.services.ml_intern_dataset_catalog_service import DatasetCatalogError
from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)


class DatasetSplitError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DatasetSplitCatalogPort(Protocol):
    @property
    def max_records(self) -> int: ...

    def open_split_source(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> Any: ...

    def split_source_descriptor(
        self, *, tenant_id: str, principal_id: str, dataset_id: str
    ) -> dict[str, Any]: ...

    def open_partition(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
    ) -> Any: ...

    def commit_split(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        train_stream: BinaryIO,
        validation_stream: BinaryIO,
        manifest: dict[str, Any],
    ) -> dict[str, Any]: ...


class MlInternDatasetSplitService:
    """Assign normalized duplicate groups with a seeded stable hash order."""

    ALGORITHM_VERSION = "normalized-group-hash-v1"

    def __init__(
        self,
        catalog: DatasetSplitCatalogPort,
        *,
        validator: MlInternDatasetValidationService | None = None,
        min_records: int = 4,
        min_validation_ratio: float = 0.05,
        max_validation_ratio: float = 0.5,
    ) -> None:
        if min_records < 2:
            raise ValueError("min_records must be >= 2")
        if not 0 < min_validation_ratio <= max_validation_ratio < 1:
            raise ValueError("validation ratio bounds are invalid")
        self._catalog = catalog
        self._validator = validator or get_dataset_validation_service()
        self._min_records = min_records
        self._min_ratio = min_validation_ratio
        self._max_ratio = max_validation_ratio

    def split(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        validation_ratio: float = 0.2,
        seed: int = 3407,
    ) -> dict[str, Any]:
        ratio = self._ratio(validation_ratio)
        normalized_seed = self._seed(seed)
        try:
            source = self._catalog.split_source_descriptor(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
            )
            group_counts: dict[str, int] = {}
            total = 0
            with self._catalog.open_split_source(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=dataset_id,
            ) as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    if not raw_line.strip():
                        continue
                    digest = _normalized_record_hash(raw_line, line_number=line_number)
                    group_counts[digest] = group_counts.get(digest, 0) + 1
                    total += 1
                    if total > self._catalog.max_records:
                        raise DatasetSplitError("record_quota_exceeded", "dataset exceeds the split record limit")
        except DatasetCatalogError as exc:
            reason = "dataset_not_found" if exc.reason_code == "invalid_dataset_id" else exc.reason_code
            raise DatasetSplitError(reason, str(exc)) from exc

        if total < self._min_records:
            raise DatasetSplitError(
                "dataset_too_small_for_split",
                f"dataset needs at least {self._min_records} records for a train/validation split",
            )
        if len(group_counts) < 2:
            raise DatasetSplitError(
                "insufficient_unique_groups",
                "dataset needs at least two distinct normalized record groups",
            )
        target_validation = max(1, min(total - 1, int(round(total * ratio))))
        ordered_groups = sorted(
            group_counts,
            key=lambda digest: hashlib.sha256(f"{normalized_seed}:{digest}".encode("ascii")).hexdigest(),
        )
        validation_groups: set[str] = set()
        validation_count = 0
        for digest in ordered_groups:
            group_size = group_counts[digest]
            if total - (validation_count + group_size) < 1:
                continue
            validation_groups.add(digest)
            validation_count += group_size
            if validation_count >= target_validation:
                break
        if not validation_groups or validation_count >= total:
            raise DatasetSplitError(
                "split_balance_impossible",
                "duplicate groups cannot form non-empty train and validation splits",
            )

        train_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        validation_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        train_digest = hashlib.sha256()
        validation_digest = hashlib.sha256()
        train_count = 0
        emitted_validation_count = 0
        try:
            try:
                with self._catalog.open_split_source(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    dataset_id=dataset_id,
                ) as handle:
                    for line_number, raw_line in enumerate(handle, start=1):
                        line = raw_line.strip()
                        if not line:
                            continue
                        digest = _normalized_record_hash(line, line_number=line_number)
                        payload = (line + "\n").encode("utf-8")
                        if digest in validation_groups:
                            validation_file.write(payload)
                            validation_digest.update(payload)
                            emitted_validation_count += 1
                        else:
                            train_file.write(payload)
                            train_digest.update(payload)
                            train_count += 1
            except DatasetCatalogError as exc:
                raise DatasetSplitError(exc.reason_code, str(exc)) from exc
            if emitted_validation_count != validation_count or train_count + validation_count != total:
                raise DatasetSplitError("split_source_changed", "dataset changed while its split was being created")
            train_file.seek(0)
            validation_file.seek(0)
            manifest = {
                "schema": "mlintern_dataset_split_manifest.v1",
                "algorithm_version": self.ALGORITHM_VERSION,
                "dataset_id": dataset_id,
                "source_sha256": source.get("sha256"),
                "seed": normalized_seed,
                "validation_ratio": ratio,
                "actual_validation_ratio": round(validation_count / total, 8),
                "source_record_count": total,
                "unique_group_count": len(group_counts),
                "duplicate_record_count": total - len(group_counts),
                "train_record_count": train_count,
                "validation_record_count": validation_count,
                "train_sha256": train_digest.hexdigest(),
                "validation_sha256": validation_digest.hexdigest(),
            }
            try:
                summary = self._catalog.commit_split(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    dataset_id=dataset_id,
                    train_stream=train_file,
                    validation_stream=validation_file,
                    manifest=manifest,
                )
            except DatasetCatalogError as exc:
                raise DatasetSplitError(exc.reason_code, str(exc)) from exc
            return {"manifest": manifest, "dataset": summary}
        finally:
            train_file.close()
            validation_file.close()

    def validate_external_validation(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        train_dataset_id: str,
        validation_dataset_id: str,
    ) -> dict[str, Any]:
        """Validate two immutable catalog sources and reject semantic overlap."""

        train_temp = tempfile.NamedTemporaryFile(mode="w+b", suffix=".jsonl", delete=False)
        validation_temp = tempfile.NamedTemporaryFile(mode="w+b", suffix=".jsonl", delete=False)
        train_path = Path(train_temp.name)
        validation_path = Path(validation_temp.name)
        try:
            with train_temp, validation_temp:
                try:
                    with self._catalog.open_split_source(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=train_dataset_id,
                    ) as source:
                        for line in source:
                            train_temp.write(line.encode("utf-8"))
                    with self._catalog.open_split_source(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=validation_dataset_id,
                    ) as source:
                        for line in source:
                            validation_temp.write(line.encode("utf-8"))
                    train_temp.flush()
                    validation_temp.flush()
                except DatasetCatalogError as exc:
                    raise DatasetSplitError(exc.reason_code, str(exc)) from exc
            train_report, validation_report, pair_errors = self._validator.validate_train_eval_pair(
                train_path,
                validation_path,
                require_secret_scan=True,
            )
            overlap = _overlap_count(train_path, validation_path)
            if overlap:
                pair_errors.append(f"train and validation datasets share {overlap} normalized record groups")
            return {
                "schema": "mlintern_external_validation_pair.v1",
                "train_dataset_id": train_dataset_id,
                "validation_dataset_id": validation_dataset_id,
                "ok": train_report.ok and validation_report.ok and not pair_errors,
                "semantic_overlap_count": overlap,
                "pair_errors": pair_errors,
                "train": _safe_validation_report(train_report.to_dict()),
                "validation": _safe_validation_report(validation_report.to_dict()),
            }
        finally:
            train_path.unlink(missing_ok=True)
            validation_path.unlink(missing_ok=True)

    def attach_external_validation(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        train_dataset_id: str,
        validation_dataset_id: str,
    ) -> dict[str, Any]:
        """Atomically bind a separately uploaded dataset as validation data.

        Both inputs come from immutable catalog split sources.  The operation
        validates the exact bytes that are committed and never exposes or
        accepts a server path.
        """

        if train_dataset_id == validation_dataset_id:
            raise DatasetSplitError(
                "validation_dataset_same_as_train",
                "validation dataset must differ from the training dataset",
            )
        train_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        validation_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        train_path: Path | None = None
        validation_path: Path | None = None
        try:
            train_count, train_sha = self._copy_split_source(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=train_dataset_id,
                destination=train_file,
            )
            validation_count, validation_sha = self._copy_split_source(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=validation_dataset_id,
                destination=validation_file,
            )
            if train_count < 1 or validation_count < 1:
                raise DatasetSplitError(
                    "external_validation_empty",
                    "training and validation datasets must both contain records",
                )
            train_file.seek(0)
            validation_file.seek(0)
            with tempfile.NamedTemporaryFile(mode="w+b", suffix=".jsonl", delete=False) as train_temp:
                train_path = Path(train_temp.name)
                for chunk in iter(lambda: train_file.read(1024 * 1024), b""):
                    train_temp.write(chunk)
            with tempfile.NamedTemporaryFile(mode="w+b", suffix=".jsonl", delete=False) as validation_temp:
                validation_path = Path(validation_temp.name)
                for chunk in iter(lambda: validation_file.read(1024 * 1024), b""):
                    validation_temp.write(chunk)
            try:
                train_report, validation_report, pair_errors = self._validator.validate_train_eval_pair(
                    train_path,
                    validation_path,
                    require_secret_scan=True,
                )
                overlap = _overlap_count(train_path, validation_path)
                if overlap:
                    pair_errors.append(
                        f"train and validation datasets share {overlap} normalized record groups"
                    )
                if not train_report.ok or not validation_report.ok or pair_errors:
                    raise DatasetSplitError(
                        "external_validation_pair_invalid",
                        "external validation dataset failed validation or overlaps training data",
                    )
                manifest = {
                    "schema": "mlintern_dataset_split_manifest.v1",
                    "algorithm_version": "external-validation-dataset-v1",
                    "dataset_id": train_dataset_id,
                    "validation_dataset_id": validation_dataset_id,
                    "source_sha256": self._catalog.split_source_descriptor(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=train_dataset_id,
                    )["sha256"],
                    "validation_source_sha256": self._catalog.split_source_descriptor(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=validation_dataset_id,
                    )["sha256"],
                    "seed": None,
                    "validation_ratio": round(validation_count / (train_count + validation_count), 8),
                    "actual_validation_ratio": round(
                        validation_count / (train_count + validation_count), 8
                    ),
                    "source_record_count": train_count,
                    "train_record_count": train_count,
                    "validation_record_count": validation_count,
                    "train_sha256": train_sha,
                    "validation_sha256": validation_sha,
                }
                train_file.seek(0)
                validation_file.seek(0)
                try:
                    summary = self._catalog.commit_split(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=train_dataset_id,
                        train_stream=train_file,
                        validation_stream=validation_file,
                        manifest=manifest,
                    )
                except DatasetCatalogError as exc:
                    raise DatasetSplitError(exc.reason_code, str(exc)) from exc
                return {
                    "manifest": manifest,
                    "dataset": summary,
                    "pair": {
                        "ok": True,
                        "semantic_overlap_count": 0,
                        "train": _safe_validation_report(train_report.to_dict()),
                        "validation": _safe_validation_report(validation_report.to_dict()),
                    },
                }
            finally:
                if train_path is not None:
                    train_path.unlink(missing_ok=True)
                if validation_path is not None:
                    validation_path.unlink(missing_ok=True)
        except DatasetCatalogError as exc:
            reason = "dataset_not_found" if exc.reason_code == "invalid_dataset_id" else exc.reason_code
            raise DatasetSplitError(reason, str(exc)) from exc
        finally:
            if train_path is not None:
                train_path.unlink(missing_ok=True)
            if validation_path is not None:
                validation_path.unlink(missing_ok=True)
            train_file.close()
            validation_file.close()

    def _copy_split_source(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        destination: BinaryIO,
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        count = 0
        with self._catalog.open_split_source(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
        ) as source:
            for raw_line in source:
                if not raw_line.strip():
                    continue
                payload = (raw_line.rstrip("\r\n") + "\n").encode("utf-8")
                destination.write(payload)
                digest.update(payload)
                count += 1
                if count > self._catalog.max_records:
                    raise DatasetSplitError(
                        "record_quota_exceeded",
                        "external validation input exceeds the record limit",
                    )
        return count, digest.hexdigest()

    def _ratio(self, value: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise DatasetSplitError("invalid_validation_ratio", "validation ratio must be numeric") from exc
        if not math.isfinite(parsed) or not self._min_ratio <= parsed <= self._max_ratio:
            raise DatasetSplitError(
                "invalid_validation_ratio",
                f"validation ratio must be between {self._min_ratio} and {self._max_ratio}",
            )
        return parsed

    @staticmethod
    def _seed(value: int) -> int:
        if isinstance(value, bool):
            raise DatasetSplitError("invalid_split_seed", "split seed must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise DatasetSplitError("invalid_split_seed", "split seed must be an integer") from exc
        if not -(2**31) <= parsed <= 2**31 - 1:
            raise DatasetSplitError("invalid_split_seed", "split seed is out of range")
        return parsed


def _normalized_record_hash(raw_line: str, *, line_number: int) -> str:
    try:
        record = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise DatasetSplitError("invalid_json", f"dataset line {line_number} is not valid JSON") from exc
    if not isinstance(record, dict):
        raise DatasetSplitError("invalid_record", f"dataset line {line_number} is not a JSON object")
    lineage_root_id = str(record.get("lineage_root_id") or "").strip()
    if lineage_root_id:
        if len(lineage_root_id) > 191:
            raise DatasetSplitError("invalid_lineage_root", f"dataset line {line_number} has invalid lineage")
        return hashlib.sha256(f"lineage-root\0{lineage_root_id}".encode()).hexdigest()
    if isinstance(record.get("messages"), list):
        normalized: dict[str, Any] = {
            "messages": [
                {
                    "role": str(message.get("role") or "").strip().lower(),
                    "content": " ".join(str(message.get("content") or "").split()),
                }
                for message in record["messages"]
                if isinstance(message, dict)
            ]
        }
    else:
        normalized = {
            "instruction": " ".join(str(record.get("instruction") or "").split()),
            "input": " ".join(str(record.get("input") or "").split()),
            "output": " ".join(str(record.get("output") or "").split()),
        }
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _overlap_count(train_path: Path, validation_path: Path) -> int:
    train_hashes: set[str] = set()
    with train_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                train_hashes.add(_normalized_record_hash(line, line_number=line_number))
    overlaps: set[str] = set()
    with validation_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                digest = _normalized_record_hash(line, line_number=line_number)
                if digest in train_hashes:
                    overlaps.add(digest)
    return len(overlaps)


def _safe_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    """Project a validation report without server paths or secret excerpts."""

    safe = dict(report)
    safe.pop("dataset_path", None)
    safe["secret_findings"] = [
        {
            "line": finding.get("line"),
            "pattern": finding.get("pattern"),
        }
        for finding in report.get("secret_findings") or []
        if isinstance(finding, dict)
    ]
    return safe
