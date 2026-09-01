"""Build curated JSONL datasets for ml_intern LoRA training.

This service intentionally does not generate new examples with an LLM. It only
normalizes explicitly supplied records/files into trainable JSONL and delegates
quality checks to MlInternDatasetValidationService.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)


class DatasetBuildError(ValueError):
    """Raised for invalid dataset build requests."""


@dataclass
class DatasetBuildResult:
    status: str
    dataset_path: str | None
    absolute_dataset_path: str | None
    report_path: str | None
    format_type: str
    total_input_records: int
    written_records: int
    duplicate_count: int
    skipped_records: list[dict[str, Any]] = field(default_factory=list)
    validation_report: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "mlintern_lora_dataset_build_result.v1"
        return payload


class MlInternLoraDatasetBuildService:
    """Normalize curated examples into a validated LoRA JSONL dataset."""

    def __init__(
        self,
        *,
        dataset_root: str | Path = "data/training/lora",
        validator: MlInternDatasetValidationService | None = None,
    ) -> None:
        self._dataset_root = Path(dataset_root)
        self._validator = validator or get_dataset_validation_service()

    def build_dataset(self, spec: dict[str, Any]) -> DatasetBuildResult:
        fmt = str(spec.get("format") or "instruction").strip().lower()
        if fmt not in {"instruction", "chat"}:
            raise DatasetBuildError("format must be 'instruction' or 'chat'")

        output_path = self._resolve_output_path(str(spec.get("output_path") or "train.jsonl"))
        min_instruction_chars = self._bounded_int(spec.get("min_instruction_chars"), default=4, low=1, high=4096)
        min_output_chars = self._bounded_int(spec.get("min_output_chars"), default=1, low=1, high=4096)
        max_examples = self._bounded_int(spec.get("max_examples"), default=1000, low=1, high=100000)
        require_secret_scan = bool(spec.get("require_secret_scan", True))

        source_records = self._collect_records(spec)
        normalized: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        duplicate_count = 0

        for idx, item in enumerate(source_records, start=1):
            record = self._normalize_record(item, fmt)
            if record is None:
                skipped.append({"index": idx, "reason": "unsupported_record_shape"})
                continue
            quality_reason = self._quality_reason(record, fmt, min_instruction_chars, min_output_chars)
            if quality_reason:
                skipped.append({"index": idx, "reason": quality_reason})
                continue
            digest = self._record_hash(record)
            if digest in seen:
                duplicate_count += 1
                continue
            seen.add(digest)
            normalized.append(record)
            if len(normalized) >= max_examples:
                break

        if not normalized:
            return DatasetBuildResult(
                status="failed",
                dataset_path=None,
                absolute_dataset_path=None,
                report_path=None,
                format_type=fmt,
                total_input_records=len(source_records),
                written_records=0,
                duplicate_count=duplicate_count,
                skipped_records=skipped,
                errors=["no usable records after normalization and quality filtering"],
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in normalized) + "\n",
            encoding="utf-8",
        )

        validation = self._validator.validate(output_path, require_secret_scan=require_secret_scan)
        report_path = output_path.with_suffix(output_path.suffix + ".validation.json")
        self._validator.write_report(validation, report_path)
        status = "completed" if validation.ok else "validation_failed"
        return DatasetBuildResult(
            status=status,
            dataset_path=self._relative_to_dataset_root(output_path),
            absolute_dataset_path=str(output_path),
            report_path=str(report_path),
            format_type=fmt,
            total_input_records=len(source_records),
            written_records=len(normalized),
            duplicate_count=duplicate_count,
            skipped_records=skipped,
            validation_report=validation.to_dict(),
            errors=[e.message for e in validation.errors],
            warnings=[w.message for w in validation.warnings],
        )

    def _collect_records(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in spec.get("records") or spec.get("examples") or []:
            if isinstance(item, dict):
                records.append(dict(item))

        source_paths = spec.get("source_paths") or spec.get("sourcePaths") or []
        if isinstance(source_paths, str):
            source_paths = [source_paths]
        for raw_path in source_paths:
            path = self._resolve_source_path(str(raw_path))
            records.extend(self._read_source_file(path))
        return records

    def _read_source_file(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            raise DatasetBuildError(f"source file not found: {path}")
        if path.suffix.lower() == ".jsonl":
            rows: list[dict[str, Any]] = []
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line_no, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DatasetBuildError(f"invalid JSONL in {path} line {line_no}: {exc}") from exc
                    if isinstance(item, dict):
                        item.setdefault("source_path", str(path))
                        rows.append(item)
            return rows

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            values = data.get("records") or data.get("examples") or data.get("items") or [data]
        else:
            values = data
        if not isinstance(values, list):
            raise DatasetBuildError(f"source file must contain a JSON object/list: {path}")
        rows = []
        for item in values:
            if isinstance(item, dict):
                copied = dict(item)
                copied.setdefault("source_path", str(path))
                rows.append(copied)
        return rows

    def _resolve_output_path(self, raw: str) -> Path:
        if not raw.strip():
            raise DatasetBuildError("output_path is required")
        root = self._dataset_root.resolve()
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        if root != resolved and root not in resolved.parents:
            raise DatasetBuildError("output_path must stay inside dataset_root")
        if resolved.suffix.lower() != ".jsonl":
            raise DatasetBuildError("output_path must end with .jsonl")
        return resolved

    def _resolve_source_path(self, raw: str) -> Path:
        if not raw.strip():
            raise DatasetBuildError("source path must not be empty")
        path = Path(raw)
        if not path.is_absolute():
            path = self._dataset_root / path
        resolved = path.resolve()
        root = self._dataset_root.resolve()
        if root != resolved and root not in resolved.parents:
            raise DatasetBuildError("source_paths must stay inside dataset_root")
        return resolved

    @staticmethod
    def _normalize_record(item: dict[str, Any], fmt: str) -> dict[str, Any] | None:
        if fmt == "chat":
            messages = item.get("messages")
            if isinstance(messages, list):
                clean_messages = []
                for message in messages:
                    if isinstance(message, dict):
                        role = str(message.get("role") or "").strip()
                        content = str(message.get("content") or "").strip()
                        if role and content:
                            clean_messages.append({"role": role, "content": content})
                if clean_messages:
                    return MlInternLoraDatasetBuildService._with_metadata({"messages": clean_messages}, item)
            instruction = str(item.get("instruction") or item.get("prompt") or item.get("input") or "").strip()
            output = str(item.get("output") or item.get("completion") or item.get("response") or "").strip()
            if instruction and output:
                return MlInternLoraDatasetBuildService._with_metadata(
                    {"messages": [{"role": "user", "content": instruction}, {"role": "assistant", "content": output}]},
                    item,
                )
            return None

        instruction = str(item.get("instruction") or item.get("prompt") or item.get("input") or "").strip()
        output = str(item.get("output") or item.get("completion") or item.get("response") or "").strip()
        if not instruction or not output:
            messages = item.get("messages")
            if isinstance(messages, list):
                user = next((m for m in messages if isinstance(m, dict) and m.get("role") == "user"), {})
                assistant = next((m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"), {})
                instruction = str(user.get("content") or "").strip()
                output = str(assistant.get("content") or "").strip()
        if not instruction or not output:
            return None
        return MlInternLoraDatasetBuildService._with_metadata(
            {"instruction": instruction, "output": output},
            item,
        )

    @staticmethod
    def _with_metadata(record: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        for key in (
            "source_path",
            "source_ref",
            "task_kind",
            "privacy_class",
            "quality_label",
            "record_digest",
            "feedback_id",
            "consent_id",
            "consent_digest",
            "lineage_root_id",
            "split",
            "recipe_version",
        ):
            if key in item and item[key] not in (None, ""):
                record[key] = item[key]
        return record

    @staticmethod
    def _quality_reason(
        record: dict[str, Any],
        fmt: str,
        min_instruction_chars: int,
        min_output_chars: int,
    ) -> str | None:
        if fmt == "chat":
            messages = record.get("messages") or []
            user_text = " ".join(
                str(m.get("content") or "") for m in messages if isinstance(m, dict) and m.get("role") == "user"
            )
            assistant_text = " ".join(
                str(m.get("content") or "") for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
            )
            if len(user_text.strip()) < min_instruction_chars:
                return "instruction_too_short"
            if len(assistant_text.strip()) < min_output_chars:
                return "output_too_short"
            return None
        if len(str(record.get("instruction") or "").strip()) < min_instruction_chars:
            return "instruction_too_short"
        if len(str(record.get("output") or "").strip()) < min_output_chars:
            return "output_too_short"
        return None

    @staticmethod
    def _record_hash(record: dict[str, Any]) -> str:
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(low, min(parsed, high))

    def _relative_to_dataset_root(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._dataset_root.resolve()))
        except ValueError:
            return str(path)


_builder_instance: MlInternLoraDatasetBuildService | None = None


def get_lora_dataset_build_service(
    dataset_root: str | Path = "data/training/lora",
) -> MlInternLoraDatasetBuildService:
    global _builder_instance
    default_root = Path("data/training/lora")
    if Path(dataset_root) != default_root:
        return MlInternLoraDatasetBuildService(dataset_root=dataset_root)
    if _builder_instance is None:
        _builder_instance = MlInternLoraDatasetBuildService(dataset_root=dataset_root)
    return _builder_instance
