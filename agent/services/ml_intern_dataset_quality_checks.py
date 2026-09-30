"""Content checks behind dataset validation reports: PII scan, train/validation
overlap, reason-code aggregation and report redaction.

Pure functions over dataset files and validator reports; the catalog decides
when to run them and persists the outcome (SRP)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email_address", re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)),
    ("ipv4_address", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")),
    ("phone_number", re.compile(r"(?<!\w)(?:\+?\d[\d ()/.-]{7,}\d)(?!\w)")),
    ("government_id", re.compile(r"\b(?:SSN|Sozialversicherungsnummer)\s*[:=]\s*[A-Z0-9 -]{6,}", re.IGNORECASE)),
)


def validation_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    source = report or {}
    return {
        "error_count": int(source.get("error_count") or 0),
        "warning_count": int(source.get("warning_count") or 0),
        "secret_finding_count": int(source.get("secret_finding_count") or 0),
    }



def scan_pii(path: Path, *, partition: str = "train") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            for reason_code, pattern in _PII_PATTERNS:
                if pattern.search(line):
                    findings.append({"partition": partition, "line": line_number, "reason_code": reason_code})
    return findings


def semantic_record_hash(line: str) -> str | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("messages"), list):
        semantic = {
            "messages": [
                {
                    "role": str(item.get("role") or "").strip().lower(),
                    "content": " ".join(str(item.get("content") or "").split()),
                }
                for item in record["messages"]
                if isinstance(item, dict)
            ]
        }
    else:
        semantic = {
            "instruction": " ".join(str(record.get("instruction") or "").split()),
            "input": " ".join(str(record.get("input") or "").split()),
            "output": " ".join(str(record.get("output") or "").split()),
        }
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def record_overlap_count(train_path: Path, validation_path: Path) -> int:
    train_hashes: set[str] = set()
    with train_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            digest = semantic_record_hash(line)
            if digest:
                train_hashes.add(digest)
    overlaps: set[str] = set()
    with validation_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            digest = semantic_record_hash(line)
            if digest and digest in train_hashes:
                overlaps.add(digest)
    return len(overlaps)


def validation_reason_codes(
    train: dict[str, Any],
    validation: dict[str, Any] | None,
    pair_errors: list[str],
    pii_findings: list[dict[str, Any]],
    override: bool,
) -> list[str]:
    reasons = {str(item.get("type") or "validation_error") for item in train.get("errors") or []}
    if validation:
        reasons.update(str(item.get("type") or "validation_error") for item in validation.get("errors") or [])
    if pair_errors:
        reasons.add("train_validation_pair_invalid")
    if pii_findings and not override:
        reasons.add("pii_detected")
    if pii_findings and override:
        reasons.add("pii_override_applied")
    return sorted(reasons)


def safe_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in report.items() if key != "dataset_path"}
    safe["secret_findings"] = [
        {
            "line": int(item.get("line") or 0),
            "pattern": str(item.get("pattern") or "secret_detected"),
        }
        for item in report.get("secret_findings") or []
        if isinstance(item, dict)
    ]
    return safe


__all__ = [
    "record_overlap_count",
    "safe_validation_report",
    "scan_pii",
    "semantic_record_hash",
    "validation_reason_codes",
    "validation_summary",
]
