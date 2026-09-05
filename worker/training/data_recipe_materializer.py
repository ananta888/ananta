"""Offline, attempt-scoped materialization of admitted Unsloth data recipes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ananta_contracts.unsloth_task import (
    unsloth_payload_sha256,
)


class DataRecipeMaterializationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class FilesystemDatasetRecipeMaterializer:
    """Read one immutable dataset mount and publish into one attempt mount."""

    _ATTEMPT_ID = re.compile(r"^unsloth-[0-9a-f]{32}$")
    _SHA256 = re.compile(r"^[0-9a-f]{64}$")
    _OBJECTIVES = frozenset(
        {
            "causal_lm",
            "vision_instruction",
            "audio_instruction",
            "embedding_pairs",
        }
    )
    _MANIFEST_FIELDS = frozenset(
        {
            "recipe_id",
            "tenant_id",
            "dataset_id",
            "dataset_hash",
            "dataset_ref",
            "dataset_partition_sha256",
            "source_id",
            "run_id",
            "objective",
            "prompt_field",
            "response_field",
            "media_field",
            "validation_fraction",
            "seed",
            "row_count",
            "normalization_version",
        }
    )

    def __init__(
        self,
        *,
        dataset_root: Path,
        attempt_output_root: Path,
        expected_attempt_id: str | None = None,
        max_dataset_bytes: int = 4 * 1024**3,
        max_output_bytes: int = 8 * 1024**3,
        max_records: int = 10_000_000,
    ) -> None:
        self._dataset_root = dataset_root.resolve()
        self._output_root = attempt_output_root.resolve()
        self._expected_attempt_id = str(expected_attempt_id).strip() if expected_attempt_id is not None else None
        self._max_dataset_bytes = int(max_dataset_bytes)
        self._max_output_bytes = int(max_output_bytes)
        self._max_records = int(max_records)
        if (
            not self._dataset_root.is_dir()
            or self._dataset_root.is_symlink()
            or not self._output_root.is_dir()
            or self._output_root.is_symlink()
            or (self._expected_attempt_id is not None and self._ATTEMPT_ID.fullmatch(self._expected_attempt_id) is None)
            or not 0 < self._max_dataset_bytes <= 100 * 1024**3
            or not 0 < self._max_output_bytes <= 200 * 1024**3
            or not 0 < self._max_records <= 10_000_000
        ):
            raise DataRecipeMaterializationError("data_recipe_materializer_config_invalid")

    def materialize(
        self,
        manifest: Mapping[str, Any],
        *,
        attempt_id: str,
    ) -> Mapping[str, Any]:
        normalized = self._validate_manifest(
            manifest,
            attempt_id=attempt_id,
        )
        destination = self._output_root / normalized["recipe_id"]
        if destination.exists():
            return self._load_existing(
                destination,
                normalized,
                attempt_id,
            )
        source = self._resolve_dataset(normalized["dataset_ref"])
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{normalized['recipe_id']}.",
                dir=str(self._output_root),
            )
        )
        try:
            result = self._write_recipe(
                source=source,
                destination=staging,
                manifest=normalized,
                attempt_id=attempt_id,
            )
            self._write_json(staging / "result.json", result)
            try:
                os.replace(staging, destination)
            except FileExistsError:
                return self._load_existing(
                    destination,
                    normalized,
                    attempt_id,
                )
            return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _validate_manifest(
        self,
        value: Mapping[str, Any],
        *,
        attempt_id: str,
    ) -> dict[str, Any]:
        manifest = dict(value)
        if (
            set(manifest) != self._MANIFEST_FIELDS
            or manifest.get("normalization_version") != "unsloth-recipe-v2"
            or self._ATTEMPT_ID.fullmatch(str(attempt_id or "")) is None
            or (self._expected_attempt_id is not None and attempt_id != self._expected_attempt_id)
        ):
            raise DataRecipeMaterializationError("data_recipe_manifest_binding_invalid")
        recipe_id = str(manifest.get("recipe_id") or "")
        unsigned = dict(manifest)
        unsigned.pop("recipe_id", None)
        try:
            expected = unsloth_payload_sha256(unsigned)
        except (TypeError, ValueError) as exc:
            raise DataRecipeMaterializationError("data_recipe_manifest_binding_invalid") from exc
        dataset_hash = manifest.get("dataset_hash")
        partition_sha256 = str(manifest.get("dataset_partition_sha256") or "")
        row_count = manifest.get("row_count")
        seed = manifest.get("seed")
        validation_fraction = manifest.get("validation_fraction")
        objective = manifest.get("objective")
        media_field = manifest.get("media_field")
        bounded_text_fields = (
            "tenant_id",
            "dataset_id",
            "source_id",
            "run_id",
            "prompt_field",
            "response_field",
        )
        if (
            recipe_id != expected
            or self._SHA256.fullmatch(recipe_id) is None
            or not isinstance(dataset_hash, str)
            or self._SHA256.fullmatch(dataset_hash) is None
            or self._SHA256.fullmatch(partition_sha256) is None
            or any(
                not self._bounded_text(
                    manifest.get(field),
                    maximum=256,
                )
                for field in bounded_text_fields
            )
            or objective not in self._OBJECTIVES
            or (
                objective
                in {
                    "vision_instruction",
                    "audio_instruction",
                }
                and not self._bounded_text(
                    media_field,
                    maximum=256,
                )
            )
            or (
                objective
                not in {
                    "vision_instruction",
                    "audio_instruction",
                }
                and media_field is not None
            )
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or not 0 < row_count <= self._max_records
            or isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 0 <= seed <= 2**31 - 1
            or isinstance(validation_fraction, bool)
            or not isinstance(
                validation_fraction,
                (int, float),
            )
            or not 0.0 < float(validation_fraction) < 0.5
        ):
            raise DataRecipeMaterializationError("data_recipe_manifest_binding_invalid")
        dataset_ref = manifest.get("dataset_ref")
        if not isinstance(dataset_ref, str) or len(dataset_ref) > 1024:
            raise DataRecipeMaterializationError("data_recipe_manifest_binding_invalid")
        self._safe_relative(dataset_ref)
        return manifest

    @staticmethod
    def _bounded_text(
        value: Any,
        *,
        maximum: int,
    ) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= maximum
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    def _resolve_dataset(self, reference: str) -> Path:
        relative = self._safe_relative(reference)
        try:
            source = (self._dataset_root / relative).resolve(strict=True)
            source.relative_to(self._dataset_root)
        except (OSError, ValueError) as exc:
            raise DataRecipeMaterializationError("data_recipe_dataset_unavailable") from exc
        if not source.is_file() or source.is_symlink():
            raise DataRecipeMaterializationError("data_recipe_dataset_unavailable")
        return source

    def _write_recipe(
        self,
        *,
        source: Path,
        destination: Path,
        manifest: Mapping[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        train_path = destination / "train.jsonl"
        validation_path = destination / "validation.jsonl"
        source_digest = hashlib.sha256()
        output_digests = {
            "train": hashlib.sha256(),
            "validation": hashlib.sha256(),
        }
        output_rows = {"train": 0, "validation": 0}
        source_bytes = 0
        output_bytes = 0
        total_rows = 0
        with (
            source.open("rb") as source_handle,
            train_path.open("wb") as train_handle,
            validation_path.open("wb") as validation_handle,
        ):
            targets = {
                "train": train_handle,
                "validation": validation_handle,
            }
            for raw_line in source_handle:
                source_bytes += len(raw_line)
                if source_bytes > self._max_dataset_bytes:
                    raise DataRecipeMaterializationError("data_recipe_dataset_size_exceeded")
                source_digest.update(raw_line)
                if not raw_line.strip():
                    raise DataRecipeMaterializationError("data_recipe_dataset_record_invalid")
                try:
                    record = json.loads(raw_line.decode("utf-8"))
                except (
                    UnicodeError,
                    json.JSONDecodeError,
                ) as exc:
                    raise DataRecipeMaterializationError("data_recipe_dataset_record_invalid") from exc
                if not isinstance(record, Mapping):
                    raise DataRecipeMaterializationError("data_recipe_dataset_record_invalid")
                total_rows += 1
                if total_rows > self._max_records:
                    raise DataRecipeMaterializationError("data_recipe_dataset_record_limit_exceeded")
                normalized = self._normalize_record(
                    record,
                    manifest,
                )
                encoded = (
                    json.dumps(
                        normalized,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
                output_bytes += len(encoded)
                if output_bytes > self._max_output_bytes:
                    raise DataRecipeMaterializationError("data_recipe_output_size_exceeded")
                split = self._split(
                    manifest,
                    total_rows - 1,
                )
                targets[split].write(encoded)
                output_digests[split].update(encoded)
                output_rows[split] += 1
            for handle in targets.values():
                handle.flush()
                os.fsync(handle.fileno())
        if source_digest.hexdigest() != str(manifest["dataset_partition_sha256"]) or total_rows != int(
            manifest["row_count"]
        ):
            raise DataRecipeMaterializationError("data_recipe_dataset_binding_mismatch")
        recipe_id = str(manifest["recipe_id"])
        return {
            "schema": "ananta.unsloth-data-recipe-result.v1",
            "recipe_id": recipe_id,
            "attempt_id": attempt_id,
            "dataset_id": str(manifest["dataset_id"]),
            "dataset_hash": str(manifest["dataset_hash"]),
            "dataset_partition_sha256": (source_digest.hexdigest()),
            "source_id": str(manifest["source_id"]),
            "run_id": str(manifest["run_id"]),
            "output_ref": recipe_id,
            "train_ref": f"{recipe_id}/train.jsonl",
            "train_sha256": output_digests["train"].hexdigest(),
            "train_rows": output_rows["train"],
            "validation_ref": (f"{recipe_id}/validation.jsonl"),
            "validation_sha256": output_digests["validation"].hexdigest(),
            "validation_rows": output_rows["validation"],
            "total_rows": total_rows,
        }

    @staticmethod
    def _normalize_record(
        record: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        prompt = record.get(str(manifest["prompt_field"]))
        response = record.get(str(manifest["response_field"]))
        if not isinstance(prompt, str) or not isinstance(
            response,
            str,
        ):
            raise DataRecipeMaterializationError("data_recipe_dataset_mapping_invalid")
        objective = str(manifest["objective"])
        if objective == "causal_lm":
            return {
                "messages": [
                    {"content": prompt, "role": "user"},
                    {
                        "content": response,
                        "role": "assistant",
                    },
                ]
            }
        if objective == "embedding_pairs":
            return {
                "anchor": prompt,
                "positive": response,
            }
        media = record.get(str(manifest.get("media_field") or ""))
        if not isinstance(media, str):
            raise DataRecipeMaterializationError("data_recipe_dataset_mapping_invalid")
        FilesystemDatasetRecipeMaterializer._safe_relative(media)
        return {
            "media": media,
            "prompt": prompt,
            "response": response,
        }

    @staticmethod
    def _split(
        manifest: Mapping[str, Any],
        index: int,
    ) -> str:
        token = hashlib.sha256((f"{manifest['recipe_id']}\0{manifest['seed']}\0{index}").encode("utf-8")).digest()
        bucket = int.from_bytes(
            token[:8],
            "big",
        ) / float(2**64)
        return "validation" if bucket < float(manifest["validation_fraction"]) else "train"

    @staticmethod
    def _safe_relative(value: str) -> PurePosixPath:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise DataRecipeMaterializationError("data_recipe_relative_path_invalid")
        return path

    @staticmethod
    def _write_json(
        path: Path,
        value: Mapping[str, Any],
    ) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(
                dict(value),
                handle,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _load_existing(
        destination: Path,
        manifest: Mapping[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        try:
            raw = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataRecipeMaterializationError("data_recipe_output_conflict") from exc
        if (
            not isinstance(raw, Mapping)
            or raw.get("recipe_id") != manifest.get("recipe_id")
            or raw.get("attempt_id") != attempt_id
            or raw.get("dataset_hash") != manifest.get("dataset_hash")
        ):
            raise DataRecipeMaterializationError("data_recipe_output_conflict")
        return dict(raw)


__all__ = [
    "DataRecipeMaterializationError",
    "FilesystemDatasetRecipeMaterializer",
]
