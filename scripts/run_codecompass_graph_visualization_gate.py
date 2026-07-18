#!/usr/bin/env python3
"""Assemble and validate the CodeCompass graph-visualization release gate.

The browser suite emits measurements only after its own assertions pass.  This
runner binds that handoff to the current budget and current source bytes, joins
it with explicit release-check evidence, and emits a deterministic report.  It
never copies logs, commands, repository text, timestamps, or host paths into
the committed evidence projection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS = ROOT / "config/codecompass/graph_visualization_budgets.v1.json"
DEFAULT_EVIDENCE = ROOT / "artifacts/test-gates/codecompass-graph-visualization-evidence.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/codecompass-graph-visualization.json"
REPORT_SCHEMA_PATH = ROOT / "schemas/testing/codecompass_graph_visualization_gate.v1.json"

BUDGET_SCHEMA = "ananta.codecompass-graph-visualization-budgets.v1"
BROWSER_MEASUREMENTS_SCHEMA = "ananta.codecompass-graph-visualization-browser-measurements.v1"
RELEASE_CHECKS_SCHEMA = "ananta.codecompass-graph-visualization-release-checks.v1"
RELEASE_CHECK_EVIDENCE_SCHEMA = "ananta.codecompass-graph-visualization-release-check-evidence.v1"
EVIDENCE_SCHEMA = "ananta.codecompass-graph-visualization-evidence.v1"
REPORT_SCHEMA = "ananta.codecompass-graph-visualization-gate.v1"

CHECK_IDS = ("functional", "security", "accessibility", "production_build")
FIXTURE_KEYS = ("nodes", "edges", "domains", "hover_events")
OPERATION_KEYS = (
    "http_requests_per_visual_interaction",
    "score_recomputations_per_hover_burst",
    "renderer_reinitializations_per_profile_change",
    "graph_data_resets_per_profile_change",
    "projection_runs_per_animation_frame",
)
TIMING_KEYS = ("initial_projection", "cached_projection", "hover_update", "profile_update")
HASH_KEYS = ("graph", "graph_repeat", "profile", "projection", "projection_repeat")

HEX_64 = re.compile(r"^[0-9a-f]{64}$")
ENVIRONMENT_CLASS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")
MAX_JSON_INPUT_BYTES = 2 * 1024 * 1024
MAX_RELEASE_EVIDENCE_BYTES = 64 * 1024 * 1024


class GateInputError(ValueError):
    """Raised when budgets or evidence are incomplete, stale, or unsafe."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path, *, maximum_bytes: int | None = None) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise GateInputError(f"evidence_file_missing:{path.name}") from exc
    if not path.is_file():
        raise GateInputError(f"evidence_path_not_file:{path.name}")
    if maximum_bytes is not None and stat.st_size > maximum_bytes:
        raise GateInputError(f"evidence_file_too_large:{path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        stat = path.stat()
        if stat.st_size > MAX_JSON_INPUT_BYTES:
            raise GateInputError(f"{label}_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateInputError(f"{label}_missing:{path.name}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise GateInputError(f"{label}_unreadable:{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise GateInputError(f"{label}_invalid_json:{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise GateInputError(f"{label}_must_be_object")
    return payload


def _load_hashed_object(
    path: Path,
    *,
    label: str,
    maximum_bytes: int = MAX_JSON_INPUT_BYTES,
) -> tuple[dict[str, Any], str]:
    """Read, hash, and decode one bounded artifact from the same byte snapshot."""

    try:
        with path.open("rb") as handle:
            raw = handle.read(maximum_bytes + 1)
    except FileNotFoundError as exc:
        raise GateInputError(f"{label}_missing:{path.name}") from exc
    except OSError as exc:
        raise GateInputError(f"{label}_unreadable:{path.name}") from exc
    if len(raw) > maximum_bytes:
        raise GateInputError(f"{label}_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise GateInputError(f"{label}_unreadable:{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise GateInputError(f"{label}_invalid_json:{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise GateInputError(f"{label}_must_be_object")
    return payload, hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateInputError(f"{path}_must_be_object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: Sequence[str] | set[str], *, path: str) -> None:
    expected_keys = set(expected)
    actual_keys = set(value)
    missing = sorted(expected_keys - actual_keys)
    unexpected = sorted(actual_keys - expected_keys)
    if missing:
        raise GateInputError(f"{path}_missing_fields:{','.join(missing)}")
    if unexpected:
        raise GateInputError(f"{path}_unexpected_fields:{','.join(unexpected)}")


def _finite_number(value: Any, *, path: str, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateInputError(f"{path}_must_be_number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (not allow_zero and result == 0.0):
        suffix = "positive_finite" if not allow_zero else "nonnegative_finite"
        raise GateInputError(f"{path}_must_be_{suffix}")
    return result


def _integer(value: Any, *, path: str, allow_zero: bool = True) -> int:
    number = _finite_number(value, path=path, allow_zero=allow_zero)
    if not number.is_integer():
        raise GateInputError(f"{path}_must_be_integer")
    return int(number)


def _required_bool(value: Any, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise GateInputError(f"{path}_must_be_boolean")
    return value


def _required_sha256(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise GateInputError(f"{path}_must_be_sha256")
    return value


def _safe_repository_path(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise GateInputError(f"{path}_must_be_safe_relative_path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise GateInputError(f"{path}_must_be_safe_relative_path")
    if not all(SAFE_PATH_SEGMENT.fullmatch(part) for part in pure.parts):
        raise GateInputError(f"{path}_must_be_safe_relative_path")
    return pure.as_posix()


def _resolve_repository_file(
    repository_root: Path,
    relative_path: str,
    *,
    path: str,
    required_prefix: tuple[str, ...] | None = None,
) -> Path:
    safe_path = _safe_repository_path(relative_path, path=path)
    parts = PurePosixPath(safe_path).parts
    if required_prefix is not None and parts[: len(required_prefix)] != required_prefix:
        raise GateInputError(f"{path}_outside_allowed_prefix")
    root = repository_root.resolve()
    candidate = (root / safe_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GateInputError(f"{path}_outside_repository") from exc
    if not candidate.is_file():
        raise GateInputError(f"{path}_missing")
    return candidate


def _relative_evidence_path(path: Path, *, repository_root: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError as exc:
        raise GateInputError("evidence_path_outside_repository") from exc
    return _safe_repository_path(relative, path="evidence_path")


def _validate_budget_contract(budgets: Mapping[str, Any]) -> tuple[str, ...]:
    _exact_keys(
        budgets,
        {
            "$schema",
            "schema",
            "fixture",
            "cache",
            "operation_limits",
            "browser_p95_ms",
            "evidence_source_paths",
        },
        path="budgets",
    )
    if budgets.get("schema") != BUDGET_SCHEMA:
        raise GateInputError("budget_schema_unsupported")
    if budgets.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise GateInputError("budget_json_schema_declaration_invalid")

    fixture = _mapping(budgets.get("fixture"), path="budgets.fixture")
    _exact_keys(fixture, FIXTURE_KEYS, path="budgets.fixture")
    for key in FIXTURE_KEYS:
        _integer(fixture.get(key), path=f"budgets.fixture.{key}", allow_zero=False)

    operations = _mapping(budgets.get("operation_limits"), path="budgets.operation_limits")
    _exact_keys(operations, OPERATION_KEYS, path="budgets.operation_limits")
    for key in OPERATION_KEYS:
        _integer(operations.get(key), path=f"budgets.operation_limits.{key}")

    timings = _mapping(budgets.get("browser_p95_ms"), path="budgets.browser_p95_ms")
    _exact_keys(timings, TIMING_KEYS, path="budgets.browser_p95_ms")
    for key in TIMING_KEYS:
        _finite_number(timings.get(key), path=f"budgets.browser_p95_ms.{key}", allow_zero=False)

    cache = _mapping(budgets.get("cache"), path="budgets.cache")
    _exact_keys(cache, {"max_revision_profile_entries"}, path="budgets.cache")
    _integer(
        cache.get("max_revision_profile_entries"),
        path="budgets.cache.max_revision_profile_entries",
        allow_zero=False,
    )

    source_paths = budgets.get("evidence_source_paths")
    if not isinstance(source_paths, list) or not source_paths:
        raise GateInputError("budgets.evidence_source_paths_must_be_nonempty_array")
    normalized = tuple(
        _safe_repository_path(value, path=f"budgets.evidence_source_paths[{index}]")
        for index, value in enumerate(source_paths)
    )
    if len(set(normalized)) != len(normalized):
        raise GateInputError("budgets.evidence_source_paths_must_be_unique")
    if tuple(sorted(normalized)) != normalized:
        raise GateInputError("budgets.evidence_source_paths_must_be_sorted")
    return normalized


def _validate_budget_binding(
    *,
    budgets: Mapping[str, Any],
    evidence: Mapping[str, Any],
    budget_path: Path,
    repository_root: Path,
) -> str:
    relative_budget_path = _relative_evidence_path(budget_path, repository_root=repository_root)
    resolved_budget = _resolve_repository_file(
        repository_root,
        relative_budget_path,
        path="budget_path",
    )
    persisted_budgets = _load_object(resolved_budget, label="budgets")
    if persisted_budgets != dict(budgets):
        raise GateInputError("budget_input_does_not_match_repository_file")
    expected_hash = _sha256_file(resolved_budget)
    observed_hash = _required_sha256(evidence.get("budget_sha256"), path="evidence.budget_sha256")
    if observed_hash != expected_hash:
        raise GateInputError("budget_evidence_stale")
    return expected_hash


def _validate_source_binding(
    *,
    source_paths: Sequence[str],
    raw_hashes: Any,
    repository_root: Path,
) -> dict[str, str]:
    hashes = _mapping(raw_hashes, path="evidence.source_hashes")
    if set(hashes) != set(source_paths):
        raise GateInputError("source_hash_projection_mismatch")
    verified: dict[str, str] = {}
    for relative_path in source_paths:
        observed = _required_sha256(hashes.get(relative_path), path=f"evidence.source_hashes.{relative_path}")
        source = _resolve_repository_file(
            repository_root,
            relative_path,
            path=f"evidence.source_hashes.{relative_path}",
        )
        expected = _sha256_file(source)
        if observed != expected:
            raise GateInputError(f"source_evidence_stale:{relative_path}")
        verified[relative_path] = observed
    return verified


def _validate_hashes(raw: Any) -> dict[str, str]:
    hashes = _mapping(raw, path="evidence.hashes")
    _exact_keys(hashes, HASH_KEYS, path="evidence.hashes")
    selected = {key: _required_sha256(hashes.get(key), path=f"evidence.hashes.{key}") for key in HASH_KEYS}
    if selected["graph"] != selected["graph_repeat"]:
        raise GateInputError("graph_hash_not_reproducible")
    if selected["projection"] != selected["projection_repeat"]:
        raise GateInputError("projection_hash_not_reproducible")
    return selected


def _evaluate_measurements(
    *,
    budgets: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[
    dict[str, int],
    bool,
    dict[str, dict[str, int]],
    bool,
    dict[str, dict[str, float]],
    bool,
    dict[str, Any],
    bool,
    dict[str, str],
]:
    fixture_budget = _mapping(budgets.get("fixture"), path="budgets.fixture")
    fixture_evidence = _mapping(evidence.get("fixture"), path="evidence.fixture")
    _exact_keys(fixture_evidence, FIXTURE_KEYS, path="evidence.fixture")
    fixture: dict[str, int] = {}
    fixture_passed = True
    for key in FIXTURE_KEYS:
        required = _integer(fixture_budget.get(key), path=f"budgets.fixture.{key}", allow_zero=False)
        measured = _integer(fixture_evidence.get(key), path=f"evidence.fixture.{key}", allow_zero=False)
        fixture[key] = measured
        fixture_passed = fixture_passed and measured >= required

    operation_budgets = _mapping(budgets.get("operation_limits"), path="budgets.operation_limits")
    operation_evidence = _mapping(evidence.get("operation_counts"), path="evidence.operation_counts")
    _exact_keys(operation_evidence, OPERATION_KEYS, path="evidence.operation_counts")
    operations: dict[str, dict[str, int]] = {}
    operations_passed = True
    for key in OPERATION_KEYS:
        limit = _integer(operation_budgets.get(key), path=f"budgets.operation_limits.{key}")
        measured = _integer(operation_evidence.get(key), path=f"evidence.operation_counts.{key}")
        operations[key] = {"measured": measured, "maximum": limit}
        operations_passed = operations_passed and measured <= limit

    timing_budgets = _mapping(budgets.get("browser_p95_ms"), path="budgets.browser_p95_ms")
    timing_evidence = _mapping(evidence.get("browser_p95_ms"), path="evidence.browser_p95_ms")
    _exact_keys(timing_evidence, TIMING_KEYS, path="evidence.browser_p95_ms")
    timings: dict[str, dict[str, float]] = {}
    timings_passed = True
    for key in TIMING_KEYS:
        limit = _finite_number(timing_budgets.get(key), path=f"budgets.browser_p95_ms.{key}", allow_zero=False)
        measured = _finite_number(timing_evidence.get(key), path=f"evidence.browser_p95_ms.{key}")
        timings[key] = {"measured": measured, "maximum": limit}
        timings_passed = timings_passed and measured <= limit

    cache_budget = _mapping(budgets.get("cache"), path="budgets.cache")
    cache_evidence = _mapping(evidence.get("cache"), path="evidence.cache")
    _exact_keys(cache_evidence, {"entries_after_eviction", "deterministic_lru_passed"}, path="evidence.cache")
    cache_limit = _integer(
        cache_budget.get("max_revision_profile_entries"),
        path="budgets.cache.max_revision_profile_entries",
        allow_zero=False,
    )
    cache_entries = _integer(cache_evidence.get("entries_after_eviction"), path="evidence.cache.entries_after_eviction")
    cache_eviction = _required_bool(
        cache_evidence.get("deterministic_lru_passed"),
        path="evidence.cache.deterministic_lru_passed",
    )
    cache_measurements = {
        "entries_after_eviction": cache_entries,
        "maximum": cache_limit,
        "deterministic_lru_passed": cache_eviction,
    }
    cache_passed = cache_entries <= cache_limit and cache_eviction
    return (
        fixture,
        fixture_passed,
        operations,
        operations_passed,
        timings,
        timings_passed,
        cache_measurements,
        cache_passed,
        _validate_hashes(evidence.get("hashes")),
    )


def _validate_release_attestation_contract(attestation: Mapping[str, Any], *, check_id: str) -> None:
    schema = _load_object(REPORT_SCHEMA_PATH, label="report_schema")
    definitions = _mapping(schema.get("$defs"), path="report_schema.$defs")
    definition = dict(
        _mapping(
            definitions.get("releaseCheckAttestation"),
            path="report_schema.$defs.releaseCheckAttestation",
        )
    )
    definition["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    definition["$defs"] = dict(definitions)
    try:
        Draft202012Validator.check_schema(definition)
    except SchemaError as exc:
        raise GateInputError("release_check_attestation_schema_definition_invalid") from exc
    if next(Draft202012Validator(definition).iter_errors(dict(attestation)), None) is not None:
        raise GateInputError(f"release_check_attestation_contract_invalid:{check_id}")


def _validate_release_check_entries(
    raw: Any,
    *,
    repository_root: Path,
    source_paths: Sequence[str],
) -> dict[str, dict[str, str]]:
    entries = _mapping(raw, path="release_check_evidence")
    _exact_keys(entries, CHECK_IDS, path="release_check_evidence")
    verified: dict[str, dict[str, str]] = {}
    for check_id in CHECK_IDS:
        entry = _mapping(entries.get(check_id), path=f"release_check_evidence.{check_id}")
        _exact_keys(entry, {"status", "evidence_path", "evidence_sha256"}, path=f"release_check_evidence.{check_id}")
        status = entry.get("status")
        if status not in {"passed", "failed"}:
            raise GateInputError(f"release_check_evidence.{check_id}.status_invalid")
        evidence_path = _safe_repository_path(
            entry.get("evidence_path"),
            path=f"release_check_evidence.{check_id}.evidence_path",
        )
        evidence_file = _resolve_repository_file(
            repository_root,
            evidence_path,
            path=f"release_check_evidence.{check_id}.evidence_path",
            required_prefix=("artifacts", "test-gates"),
        )
        observed_hash = _required_sha256(
            entry.get("evidence_sha256"),
            path=f"release_check_evidence.{check_id}.evidence_sha256",
        )
        attestation, expected_hash = _load_hashed_object(
            evidence_file,
            label=f"release_check_attestation.{check_id}",
            maximum_bytes=min(MAX_JSON_INPUT_BYTES, MAX_RELEASE_EVIDENCE_BYTES),
        )
        if observed_hash != expected_hash:
            raise GateInputError(f"release_check_evidence_stale:{check_id}")
        _exact_keys(
            attestation,
            {"schema", "check_id", "status", "source_hashes"},
            path=f"release_check_attestation.{check_id}",
        )
        if attestation.get("schema") != RELEASE_CHECK_EVIDENCE_SCHEMA:
            raise GateInputError(f"release_check_attestation_schema_unsupported:{check_id}")
        _validate_release_attestation_contract(attestation, check_id=check_id)
        if attestation.get("check_id") != check_id:
            raise GateInputError(f"release_check_attestation_check_id_mismatch:{check_id}")
        if attestation.get("status") != status:
            raise GateInputError(f"release_check_attestation_status_mismatch:{check_id}")
        _validate_source_binding(
            source_paths=source_paths,
            raw_hashes=attestation.get("source_hashes"),
            repository_root=repository_root,
        )
        verified[check_id] = {
            "status": status,
            "evidence_path": evidence_path,
            "evidence_sha256": observed_hash,
        }
    return verified


def _load_release_checks(
    path: Path,
    *,
    repository_root: Path,
    source_paths: Sequence[str],
) -> dict[str, dict[str, str]]:
    payload = _load_object(path, label="release_checks")
    _exact_keys(payload, {"schema", "checks"}, path="release_checks")
    if payload.get("schema") != RELEASE_CHECKS_SCHEMA:
        raise GateInputError("release_checks_schema_unsupported")
    return _validate_release_check_entries(
        payload.get("checks"),
        repository_root=repository_root,
        source_paths=source_paths,
    )


def _validate_browser_measurements(
    *,
    budgets: Mapping[str, Any],
    measurements: Mapping[str, Any],
    budget_path: Path,
    repository_root: Path,
) -> tuple[dict[str, str], str]:
    _exact_keys(
        measurements,
        {
            "schema",
            "environment_class",
            "budget_sha256",
            "fixture",
            "operation_counts",
            "browser_p95_ms",
            "cache",
            "hashes",
            "source_hashes",
        },
        path="browser_measurements",
    )
    if measurements.get("schema") != BROWSER_MEASUREMENTS_SCHEMA:
        raise GateInputError("browser_measurements_schema_unsupported")
    environment_class = measurements.get("environment_class")
    if not isinstance(environment_class, str) or not ENVIRONMENT_CLASS.fullmatch(environment_class):
        raise GateInputError("environment_class_invalid")
    source_paths = _validate_budget_contract(budgets)
    budget_hash = _validate_budget_binding(
        budgets=budgets,
        evidence=measurements,
        budget_path=budget_path,
        repository_root=repository_root,
    )
    source_hashes = _validate_source_binding(
        source_paths=source_paths,
        raw_hashes=measurements.get("source_hashes"),
        repository_root=repository_root,
    )
    _evaluate_measurements(budgets=budgets, evidence=measurements)
    return source_hashes, budget_hash


def assemble_evidence(
    *,
    budget_path: Path,
    measurements_path: Path,
    release_checks_path: Path,
    repository_root: Path = ROOT,
) -> dict[str, Any]:
    """Join a browser handoff and explicit, hash-bound release-check inputs."""

    budgets = _load_object(budget_path, label="budgets")
    measurements = _load_object(measurements_path, label="browser_measurements")
    source_hashes, budget_hash = _validate_browser_measurements(
        budgets=budgets,
        measurements=measurements,
        budget_path=budget_path,
        repository_root=repository_root,
    )
    release_check_evidence = _load_release_checks(
        release_checks_path,
        repository_root=repository_root,
        source_paths=tuple(source_hashes),
    )
    checks = {check_id: release_check_evidence[check_id]["status"] == "passed" for check_id in CHECK_IDS}
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "environment_class": measurements["environment_class"],
        "budget_sha256": budget_hash,
        "browser_measurements_sha256": _sha256_file(measurements_path),
        "fixture": dict(_mapping(measurements["fixture"], path="browser_measurements.fixture")),
        "operation_counts": dict(
            _mapping(measurements["operation_counts"], path="browser_measurements.operation_counts")
        ),
        "browser_p95_ms": dict(_mapping(measurements["browser_p95_ms"], path="browser_measurements.browser_p95_ms")),
        "cache": dict(_mapping(measurements["cache"], path="browser_measurements.cache")),
        "hashes": dict(_mapping(measurements["hashes"], path="browser_measurements.hashes")),
        "source_hashes": source_hashes,
        "checks": checks,
        "release_check_evidence": release_check_evidence,
    }
    _validate_final_evidence(
        budgets=budgets,
        evidence=evidence,
        budget_path=budget_path,
        repository_root=repository_root,
    )
    _canonical_bytes(evidence)
    return evidence


def _validate_final_evidence(
    *,
    budgets: Mapping[str, Any],
    evidence: Mapping[str, Any],
    budget_path: Path,
    repository_root: Path,
) -> tuple[str, dict[str, str], dict[str, dict[str, str]], dict[str, bool]]:
    _exact_keys(
        evidence,
        {
            "schema",
            "environment_class",
            "budget_sha256",
            "browser_measurements_sha256",
            "fixture",
            "operation_counts",
            "browser_p95_ms",
            "cache",
            "hashes",
            "source_hashes",
            "checks",
            "release_check_evidence",
        },
        path="evidence",
    )
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise GateInputError("evidence_schema_unsupported")
    environment_class = evidence.get("environment_class")
    if not isinstance(environment_class, str) or not ENVIRONMENT_CLASS.fullmatch(environment_class):
        raise GateInputError("environment_class_invalid")
    _required_sha256(evidence.get("browser_measurements_sha256"), path="evidence.browser_measurements_sha256")
    source_paths = _validate_budget_contract(budgets)
    budget_hash = _validate_budget_binding(
        budgets=budgets,
        evidence=evidence,
        budget_path=budget_path,
        repository_root=repository_root,
    )
    source_hashes = _validate_source_binding(
        source_paths=source_paths,
        raw_hashes=evidence.get("source_hashes"),
        repository_root=repository_root,
    )
    release_evidence = _validate_release_check_entries(
        evidence.get("release_check_evidence"),
        repository_root=repository_root,
        source_paths=source_paths,
    )
    checks_raw = _mapping(evidence.get("checks"), path="evidence.checks")
    _exact_keys(checks_raw, CHECK_IDS, path="evidence.checks")
    checks = {
        check_id: _required_bool(checks_raw.get(check_id), path=f"evidence.checks.{check_id}") for check_id in CHECK_IDS
    }
    for check_id in CHECK_IDS:
        expected = release_evidence[check_id]["status"] == "passed"
        if checks[check_id] is not expected:
            raise GateInputError(f"release_check_status_mismatch:{check_id}")
    return budget_hash, source_hashes, release_evidence, checks


def _validate_report_schema(report: Mapping[str, Any], *, schema_path: Path = REPORT_SCHEMA_PATH) -> None:
    schema = _load_object(schema_path, label="report_schema")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise GateInputError(f"report_schema_invalid:{exc.message}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(dict(report)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise GateInputError(f"report_contract_invalid:{location}:{first.message}")


def build_report(
    *,
    budgets: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_path: Path,
    budget_path: Path = DEFAULT_BUDGETS,
    repository_root: Path = ROOT,
    report_schema_path: Path = REPORT_SCHEMA_PATH,
) -> dict[str, Any]:
    budget_hash, source_hashes, release_evidence, checks = _validate_final_evidence(
        budgets=budgets,
        evidence=evidence,
        budget_path=budget_path,
        repository_root=repository_root,
    )
    (
        fixture,
        fixture_passed,
        operations,
        operations_passed,
        timings,
        timings_passed,
        cache_measurements,
        cache_passed,
        hashes,
    ) = _evaluate_measurements(budgets=budgets, evidence=evidence)
    checks_passed = all(checks.values())

    gates = [
        {"gate_id": "fixture_coverage", "status": "passed" if fixture_passed else "failed", "measurements": fixture},
        {
            "gate_id": "operation_counts",
            "status": "passed" if operations_passed else "failed",
            "measurements": operations,
        },
        {"gate_id": "browser_p95", "status": "passed" if timings_passed else "failed", "measurements_ms": timings},
        {
            "gate_id": "cache_eviction",
            "status": "passed" if cache_passed else "failed",
            "measurements": cache_measurements,
        },
        {"gate_id": "release_checks", "status": "passed" if checks_passed else "failed", "measurements": checks},
        {"gate_id": "reproducibility", "status": "passed", "measurements": hashes},
    ]
    passed = all(item["status"] == "passed" for item in gates)
    report = {
        "schema": REPORT_SCHEMA,
        "gate_id": "codecompass-graph-visualization",
        "status": "passed" if passed else "failed",
        "environment_class": evidence["environment_class"],
        "budget_sha256": budget_hash,
        "evidence_sha256": _stable_hash(dict(evidence)),
        "browser_measurements_sha256": evidence["browser_measurements_sha256"],
        "evidence_paths": [_relative_evidence_path(evidence_path, repository_root=repository_root)],
        "source_hashes": source_hashes,
        "release_check_evidence": release_evidence,
        "gates": gates,
    }
    _canonical_bytes(report)
    _validate_report_schema(report, schema_path=report_schema_path)
    return report


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace one JSON artifact without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = _canonical_bytes(dict(payload))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--measurements",
        type=Path,
        help="Browser handoff emitted through CCGV_MEASUREMENTS_OUTPUT.",
    )
    parser.add_argument(
        "--release-checks",
        type=Path,
        help="Explicit hash-bound functional, security, accessibility and build evidence.",
    )
    parser.add_argument("--check-only", action="store_true", help="Validate without writing evidence or report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    assembling = args.measurements is not None or args.release_checks is not None
    if (args.measurements is None) != (args.release_checks is None):
        print(
            "codecompass-graph-visualization-gate-invalid:measurements_and_release_checks_required_together",
            file=sys.stderr,
        )
        return 2
    try:
        budgets = _load_object(args.budgets, label="budgets")
        if assembling:
            evidence = assemble_evidence(
                budget_path=args.budgets,
                measurements_path=args.measurements,
                release_checks_path=args.release_checks,
                repository_root=ROOT,
            )
        else:
            evidence = _load_object(args.evidence, label="evidence")
        report = build_report(
            budgets=budgets,
            evidence=evidence,
            evidence_path=args.evidence,
            budget_path=args.budgets,
            repository_root=ROOT,
        )
        if not args.check_only:
            if assembling:
                _atomic_write_json(args.evidence, evidence)
            _atomic_write_json(args.output, report)
    except GateInputError as exc:
        print(f"codecompass-graph-visualization-gate-invalid:{exc}", file=sys.stderr)
        return 2
    print(f"codecompass-graph-visualization-gate-{report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
