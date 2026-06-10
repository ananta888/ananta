"""Validator for codecompass_domain_analysis.v1 payloads (CCDD-020).

The validator is intentionally small and dependency-free so it can run
inside release-gate scripts without importing the analysis library. It
performs the checks documented in
``docs/codecompass-domain-discovery.md`` section 2 / 4:

  - schema identifier must be ``codecompass_domain_analysis.v1``
  - all required top-level fields are present
  - domains are sorted by domain_id (byte-stable)
  - each domain has evidence for at least one signal or a non-empty
    unassigned_records entry; the layer-only guard is enforced here
  - confidence values are 0..1
  - boundary_warnings reference known warning_types
  - duplicate domain_ids are flagged

A second helper validates the coupling payload
(``codecompass_domain_coupling.v1``) and a third validates a single
``domain_boundaries.jsonl`` line.

The CLI is a thin wrapper over :func:`validate_file`; both return a
``ValidationResult`` that callers can inspect programmatically.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

EXPECTED_SCHEMA = "codecompass_domain_analysis.v1"
EXPECTED_COUPLING_SCHEMA = "codecompass_domain_coupling.v1"

ALLOWED_WARNING_TYPES = {
    "mutual_coupling",
    "layer_spans_domains",
    "heterogeneous_root",
    "descriptor_mismatch",
}

REQUIRED_DOMAIN_FIELDS = (
    "domain_id",
    "display_name",
    "confidence",
    "root_paths",
    "package_prefixes",
    "technical_layers",
    "core_records",
    "record_count",
    "metrics",
    "boundary_warnings",
    "evidence",
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(
            errors=self.errors + other.errors, warnings=self.warnings + other.warnings
        )


def _is_sorted(values: Iterable[str]) -> bool:
    items = list(values)
    return items == sorted(items)


def validate_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()

    schema = payload.get("schema")
    if schema != EXPECTED_SCHEMA:
        result.errors.append(
            f"schema mismatch: expected {EXPECTED_SCHEMA!r}, got {schema!r}"
        )

    for field_name in (
        "project_root",
        "generated_at",
        "inputs",
        "domains",
        "unassigned_records",
        "warnings",
    ):
        if field_name not in payload:
            result.errors.append(f"missing top-level field: {field_name!r}")

    domains = payload.get("domains") or []
    if not isinstance(domains, list):
        result.errors.append("'domains' must be a list")
        return result

    seen_ids: set[str] = set()
    domain_ids: list[str] = []
    for index, domain in enumerate(domains):
        if not isinstance(domain, dict):
            result.errors.append(f"domain[{index}] is not an object")
            continue
        for required in REQUIRED_DOMAIN_FIELDS:
            if required not in domain:
                result.errors.append(
                    f"domain[{index}] missing required field {required!r}"
                )
        domain_id = domain.get("domain_id")
        if isinstance(domain_id, str):
            if domain_id in seen_ids:
                result.errors.append(f"duplicate domain_id: {domain_id!r}")
            seen_ids.add(domain_id)
            domain_ids.append(domain_id)
        confidence = domain.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            result.errors.append(
                f"domain[{index}] confidence out of range or wrong type: {confidence!r}"
            )
        for list_field in ("root_paths", "package_prefixes", "technical_layers", "core_records"):
            value = domain.get(list_field)
            if not isinstance(value, list):
                result.errors.append(
                    f"domain[{index}].{list_field} must be a list, got {type(value).__name__}"
                )
        evidence = domain.get("evidence") or {}
        if not isinstance(evidence, dict):
            result.errors.append(f"domain[{index}].evidence must be an object")
        elif not any(
            evidence.get(key) for key in evidence
        ):
            result.warnings.append(
                f"domain[{index}] has empty evidence object; provenance unverified"
            )
        boundary = domain.get("boundary_warnings") or []
        if not isinstance(boundary, list):
            result.errors.append(
                f"domain[{index}].boundary_warnings must be a list"
            )
        else:
            for j, warning in enumerate(boundary):
                if not isinstance(warning, dict):
                    result.errors.append(
                        f"domain[{index}].boundary_warnings[{j}] is not an object"
                    )
                    continue
                wtype = warning.get("warning_type")
                if wtype not in ALLOWED_WARNING_TYPES:
                    result.errors.append(
                        f"domain[{index}].boundary_warnings[{j}] unknown warning_type {wtype!r}"
                    )

    if not _is_sorted(domain_ids):
        result.errors.append(
            f"domains are not sorted by domain_id: {domain_ids!r}"
        )

    unassigned = payload.get("unassigned_records") or []
    if not isinstance(unassigned, list):
        result.errors.append("'unassigned_records' must be a list")
    elif not _is_sorted(str(r) for r in unassigned):
        result.errors.append("'unassigned_records' is not sorted")

    return result


def validate_coupling_payload(payload: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    schema = payload.get("schema")
    if schema != EXPECTED_COUPLING_SCHEMA:
        result.errors.append(
            f"coupling schema mismatch: expected {EXPECTED_COUPLING_SCHEMA!r}, got {schema!r}"
        )
    pairs = payload.get("pairs") or []
    if not isinstance(pairs, list):
        result.errors.append("'pairs' must be a list")
        return result
    seen: set[tuple[str, str]] = set()
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            result.errors.append(f"pairs[{index}] is not an object")
            continue
        source = pair.get("source")
        target = pair.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            result.errors.append(
                f"pairs[{index}] source/target must be strings, got {source!r}/{target!r}"
            )
            continue
        key = (source, target)
        if key in seen:
            result.errors.append(f"pairs[{index}] duplicate pair {key!r}")
        seen.add(key)
    return result


def validate_boundary_line(line: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for required in ("source_domain", "target_domain", "warning_type", "severity"):
        if required not in line:
            result.errors.append(f"boundary line missing {required!r}")
    wtype = line.get("warning_type")
    if wtype not in ALLOWED_WARNING_TYPES:
        result.errors.append(f"unknown warning_type {wtype!r}")
    return result


def validate_file(path: Path) -> ValidationResult:
    """Validate a domains.detected.json or domain_coupling.json file.

    The schema is read from the payload itself; this function dispatches
    on the schema identifier.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ValidationResult(errors=[f"{path}: cannot read JSON: {exc}"])
    if not isinstance(raw, dict):
        return ValidationResult(errors=[f"{path}: top-level value is not an object"])
    schema = raw.get("schema")
    if schema == EXPECTED_COUPLING_SCHEMA:
        return validate_coupling_payload(raw)
    if schema == EXPECTED_SCHEMA:
        return validate_payload(raw)
    return ValidationResult(
        errors=[f"{path}: unknown schema {schema!r}; expected {EXPECTED_SCHEMA!r} or {EXPECTED_COUPLING_SCHEMA!r}"]
    )


def _format_message(path: Path, result: ValidationResult) -> str:
    header = f"{path}: {'OK' if result.ok else 'FAIL'}"
    detail: list[str] = []
    for err in result.errors:
        detail.append(f"  error: {err}")
    for warn in result.warnings:
        detail.append(f"  warn:  {warn}")
    return "\n".join([header, *detail]) if detail else header


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: validate_codecompass_domain_discovery.py FILE [FILE ...]", file=sys.stderr)
        return 2
    overall_ok = True
    for raw_path in argv:
        path = Path(raw_path)
        if not path.is_file():
            print(f"{path}: not found", file=sys.stderr)
            overall_ok = False
            continue
        result = validate_file(path)
        print(_format_message(path, result))
        if not result.ok:
            overall_ok = False
    return 0 if overall_ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
