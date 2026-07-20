#!/usr/bin/env python3
"""Cross-runtime contract gate using one deterministic fixture catalog."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "tests/fixtures/semantic_media_contracts/catalog.v1.json"
DOMAIN_VECTORS = ROOT / "tests/fixtures/semantic_media_contracts/domain-vectors.v1.json"
DOMAINS = frozenset(
    {"envelope", "permission", "contract", "lease", "scene", "speech", "evidence", "reconciliation", "training"}
)
REQUIRED_CASES = frozenset({"valid", "unknown-field"})
REQUIRED_GOLDEN_CASES = frozenset(
    {"valid", "maximum-bound", "unknown-field", "integer-overflow", "stale-time"}
)
RUNTIME_COMMANDS: Mapping[str, Sequence[str]] = {
    "python": (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/contracts/test_semantic_media_cross_runtime.py",
    ),
    "typescript": (
        "npx",
        "vitest",
        "run",
        "src/app/services/semantic-media-cross-runtime.spec.ts",
    ),
    "worker": (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/worker/test_semantic_media_contract_catalog.py",
    ),
}

PRODUCTION_SOURCE_FILES = frozenset(
    {
        "agent/services/semantic_media_program_evidence.py",
        "scripts/run_semantic_media_contract_gate.py",
        "tests/contracts/test_semantic_media_cross_runtime.py",
        "tests/worker/test_semantic_media_contract_catalog.py",
        "tests/speech_adaptation_support.py",
        "tests/fixtures/semantic_media_contracts/catalog.v1.json",
        "tests/fixtures/semantic_media_contracts/domain-vectors.v1.json",
        "ananta_contracts/webrtc_security.py",
        "ananta_contracts/semantic_compute.py",
        "ananta_contracts/semantic_visual.py",
        "ananta_contracts/semantic_speech.py",
        "ananta_contracts/speech_evidence_sync.py",
        "ananta_contracts/speech_reconciliation.py",
        "ananta_contracts/speech_adaptation.py",
        "worker/semantic_media/handler.py",
        "worker/speech_training/runner.py",
        "frontend-angular/src/app/services/semantic-media-cross-runtime.spec.ts",
        "frontend-angular/src/app/services/webrtc-secure-envelope.ts",
        "frontend-angular/src/app/services/peer-capability.service.ts",
        "frontend-angular/src/app/services/peer-capability.types.ts",
        "frontend-angular/src/app/services/semantic-compute-contract-api.service.ts",
        "frontend-angular/src/app/services/semantic-scene-model.ts",
        "frontend-angular/src/app/services/semantic-speech-transport.service.ts",
        "frontend-angular/src/app/services/speech-evidence-sync.validators.ts",
        "frontend-angular/src/app/services/speech-reconciliation-api.service.ts",
        "frontend-angular/src/app/services/speech-adapter-registry-api.service.ts",
    }
)


def contract_source_paths(catalog: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every fixture, runtime test, schema and production parser bound by the gate."""

    paths = set(PRODUCTION_SOURCE_FILES)
    paths.add(str(catalog.get("domain_vectors_reference", "")))
    rows = catalog.get("domains")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for field in ("schema_reference", "python_test", "typescript_test", "worker_test"):
                value = row.get(field)
                if isinstance(value, str) and value:
                    paths.add(value)
    return tuple(sorted(paths))


def validate_catalog(
    catalog: Mapping[str, Any],
    *,
    domain_vectors: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> tuple[str, dict[str, int]]:
    if set(catalog) != {
        "schema",
        "canonical_utf8",
        "vectors",
        "domains",
        "domain_vectors_reference",
    }:
        raise ProgramEvidenceError("contract_catalog_shape_invalid")
    if catalog.get("schema") != "ananta.semantic-media-contract-catalog.v1":
        raise ProgramEvidenceError("contract_catalog_version_invalid")
    golden = catalog.get("canonical_utf8")
    if not isinstance(golden, Mapping) or set(golden) != {"value", "json", "sha256"}:
        raise ProgramEvidenceError("contract_canonical_fixture_invalid")
    rendered = json.dumps(golden["value"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if rendered != golden["json"] or canonical_sha256(golden["value"]) != golden["sha256"]:
        raise ProgramEvidenceError("contract_canonical_fixture_mismatch")
    vectors = catalog.get("vectors")
    if not isinstance(vectors, list) or len(vectors) < 2:
        raise ProgramEvidenceError("contract_vectors_incomplete")
    vector_ids: set[str] = set()
    for vector in vectors:
        if not isinstance(vector, Mapping) or set(vector) != {
            "id", "input", "canonical_json", "sha256", "purpose"
        }:
            raise ProgramEvidenceError("contract_vector_shape_invalid")
        vector_id = str(vector["id"])
        if vector_id in vector_ids:
            raise ProgramEvidenceError("contract_vector_duplicate")
        vector_ids.add(vector_id)
        rendered_vector = json.dumps(
            vector["input"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if (
            rendered_vector != vector["canonical_json"]
            or canonical_sha256(vector["input"]) != vector["sha256"]
        ):
            raise ProgramEvidenceError("contract_vector_canonical_mismatch")
        if vector["purpose"] != "canonical-json-and-hash":
            raise ProgramEvidenceError("contract_vector_purpose_invalid")
    rows = catalog.get("domains")
    if not isinstance(rows, list):
        raise ProgramEvidenceError("contract_domains_invalid")
    names: set[str] = set()
    case_count = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "name",
            "schema_reference",
            "python_test",
            "typescript_test",
            "worker_test",
            "cases",
        }:
            raise ProgramEvidenceError("contract_domain_shape_invalid")
        name = str(row["name"])
        if name in names:
            raise ProgramEvidenceError("contract_domain_duplicate")
        names.add(name)
        cases = row["cases"]
        if not isinstance(cases, list) or not REQUIRED_CASES <= set(cases):
            raise ProgramEvidenceError("contract_cases_incomplete")
        case_count += len(cases)
        for field in ("schema_reference", "python_test", "typescript_test", "worker_test"):
            relative = row[field]
            if relative is None:
                if field == "worker_test":
                    continue
                raise ProgramEvidenceError("contract_runtime_reference_missing")
            path = Path(str(relative))
            if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
                raise ProgramEvidenceError("contract_runtime_reference_missing")
    if names != DOMAINS:
        raise ProgramEvidenceError("contract_domain_coverage_missing")
    reference = catalog.get("domain_vectors_reference")
    if reference != "tests/fixtures/semantic_media_contracts/domain-vectors.v1.json":
        raise ProgramEvidenceError("contract_domain_vectors_reference_invalid")
    vector_path = root / str(reference)
    if domain_vectors is None:
        try:
            domain_vectors = json.loads(vector_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProgramEvidenceError("contract_domain_vectors_unavailable") from exc
    domain_measurements = validate_domain_vectors(domain_vectors)
    return canonical_sha256({"catalog": catalog, "domain_vectors": domain_vectors}), {
        "domain_count": len(names),
        "case_count": case_count,
        "shared_vector_count": len(vectors),
        **domain_measurements,
    }


def validate_domain_vectors(vectors: Mapping[str, Any]) -> dict[str, int]:
    """Validate the portable rules without deciding any golden outcome.

    Each runtime owns an independent evaluator.  This function deliberately
    checks only fixture integrity and coverage, so one Python implementation
    cannot make Python, browser and worker conformance pass together.
    """

    if set(vectors) != {"schema", "reference_clock_ms", "domains"}:
        raise ProgramEvidenceError("contract_domain_vectors_shape_invalid")
    if vectors.get("schema") != "ananta.semantic-media-domain-vectors.v1":
        raise ProgramEvidenceError("contract_domain_vectors_version_invalid")
    reference_clock = vectors.get("reference_clock_ms")
    if type(reference_clock) is not int or not 1 <= reference_clock <= 9_007_199_254_740_991:
        raise ProgramEvidenceError("contract_domain_vectors_clock_invalid")
    rows = vectors.get("domains")
    if not isinstance(rows, list):
        raise ProgramEvidenceError("contract_domain_vectors_invalid")
    names: set[str] = set()
    case_ids: set[str] = set()
    total = 0
    non_finite = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "name", "allowed_fields", "integer_rule", "time_rule", "vectors"
        }:
            raise ProgramEvidenceError("contract_domain_vector_row_invalid")
        name = str(row["name"])
        if name in names:
            raise ProgramEvidenceError("contract_domain_vector_duplicate")
        names.add(name)
        allowed = row["allowed_fields"]
        if (
            not isinstance(allowed, list)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or any(not isinstance(value, str) or not value for value in allowed)
        ):
            raise ProgramEvidenceError("contract_domain_vector_fields_invalid")
        integer_rule = row["integer_rule"]
        if not isinstance(integer_rule, Mapping) or set(integer_rule) != {"field", "minimum", "maximum"}:
            raise ProgramEvidenceError("contract_domain_vector_integer_rule_invalid")
        integer_field = integer_rule.get("field")
        minimum = integer_rule.get("minimum")
        maximum = integer_rule.get("maximum")
        if (
            integer_field not in allowed
            or type(minimum) is not int
            or type(maximum) is not int
            or minimum > maximum
            or maximum > 9_007_199_254_740_991
        ):
            raise ProgramEvidenceError("contract_domain_vector_integer_rule_invalid")
        time_rule = row["time_rule"]
        if not isinstance(time_rule, Mapping) or set(time_rule) != {
            "field", "mode", "max_delta_ms"
        }:
            raise ProgramEvidenceError("contract_domain_vector_time_rule_invalid")
        time_field = time_rule.get("field")
        mode = time_rule.get("mode")
        max_delta_ms = time_rule.get("max_delta_ms")
        if (
            time_field not in allowed
            or mode not in {"expires_after_clock", "issued_not_future"}
            or type(max_delta_ms) is not int
            or max_delta_ms < 1
        ):
            raise ProgramEvidenceError("contract_domain_vector_time_rule_invalid")
        cases = row["vectors"]
        if not isinstance(cases, list):
            raise ProgramEvidenceError("contract_domain_vector_cases_invalid")
        case_names: set[str] = set()
        for case in cases:
            if not isinstance(case, Mapping) or set(case) != {"id", "case", "input", "expected"}:
                raise ProgramEvidenceError("contract_domain_vector_case_invalid")
            case_id = str(case["id"])
            if case_id in case_ids:
                raise ProgramEvidenceError("contract_domain_vector_case_duplicate")
            case_ids.add(case_id)
            case_name = str(case["case"])
            case_names.add(case_name)
            payload = case["input"]
            expected = case["expected"]
            if not isinstance(payload, Mapping) or not isinstance(expected, Mapping) or set(expected) != {
                "accepted", "reason_code"
            } or type(expected.get("accepted")) is not bool:
                raise ProgramEvidenceError("contract_domain_vector_case_invalid")
            if any(isinstance(value, (dict, list)) for value in payload.values()):
                raise ProgramEvidenceError("contract_domain_vector_not_minimal")
            if "__NON_FINITE__" in payload.values():
                non_finite += 1
            total += 1
        if not REQUIRED_GOLDEN_CASES <= case_names:
            raise ProgramEvidenceError("contract_domain_vector_coverage_missing")
    if names != DOMAINS:
        raise ProgramEvidenceError("contract_domain_vector_domain_coverage_missing")
    if non_finite < 3:
        raise ProgramEvidenceError("contract_domain_vector_non_finite_coverage_missing")
    return {
        "domain_vector_count": total,
        "non_finite_vector_count": non_finite,
    }


def run_gate(*, execute: bool, timeout_seconds: int = 180) -> GateEvidence:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    domain_vectors = json.loads(DOMAIN_VECTORS.read_text(encoding="utf-8"))
    config_digest, measurements = validate_catalog(catalog, domain_vectors=domain_vectors)
    bound_sources = contract_source_paths(catalog)
    source_digest = source_hash(ROOT, bound_sources)
    if not execute:
        return unavailable_evidence(
            "ASMP-QA-004",
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="cross_runtime_execution_not_requested",
        )
    reasons: list[str] = []
    totals: dict[str, int] = {**measurements, "source_file_count": len(bound_sources)}
    for runtime, command in RUNTIME_COMMANDS.items():
        executable = command[0]
        if shutil.which(executable) is None:
            reasons.append(f"{runtime}_runtime_unavailable")
            totals[f"{runtime}_exit_code"] = 127
            continue
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(command),
                cwd=ROOT / "frontend-angular" if runtime == "typescript" else ROOT,
                check=False,
                capture_output=True,
                timeout=timeout_seconds,
            )
            totals[f"{runtime}_exit_code"] = completed.returncode
            totals[f"{runtime}_duration_ms"] = int((time.monotonic() - started) * 1000)
            if completed.returncode != 0:
                reasons.append(f"{runtime}_contract_gate_failed")
        except subprocess.TimeoutExpired:
            totals[f"{runtime}_exit_code"] = 124
            reasons.append(f"{runtime}_contract_gate_timeout")
    status = "passed" if not reasons else "failed"
    return GateEvidence(
        gate_id="ASMP-QA-004",
        status=status,
        reason_codes=tuple(sorted(reasons)),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements=totals,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    try:
        evidence = run_gate(execute=args.execute, timeout_seconds=args.timeout_seconds)
    except (OSError, json.JSONDecodeError, ProgramEvidenceError) as exc:
        print(
            json.dumps(
                {"status": "failed", "reason_code": getattr(exc, "reason_code", "contract_gate_input_invalid")},
                sort_keys=True,
            )
        )
        return 1
    if args.output:
        write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
