#!/usr/bin/env python3
"""Derive conservative, unapproved SFU broadcast capacity candidates.

The production input is a bounded JSON document with this top-level shape::

    {
      "schema": "ananta.sfu-broadcast-capacity-evidence-bundle.v1",
      "attestation_policy": {"signature_required": false},
      "environments": [
        {
          "topology": "...",
          "infrastructure_profile": "...",
          "digests": {
            "git_source_digest": "<sha256>",
            "config_digest": "<sha256>",
            "image_digest": "<sha256>",
            "infrastructure_digest": "<sha256>"
          },
          "gate_evidence": [...],
          "measurements": [...]
        }
      ]
    }

Every gate record and measurement must describe an attested real execution and
carry a fresh, complete release-evidence verification receipt with exactly the
environment digests. A configured signature policy is never silently relaxed.
The generated profiles remain candidates; this script has no activation, DB,
bootstrap, or admission-control side effect.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any


INPUT_SCHEMA = "ananta.sfu-broadcast-capacity-evidence-bundle.v1"
OUTPUT_SCHEMA = "ananta.sfu-broadcast-capacity-profiles.v1"
GATE_REPORT_SCHEMA = "ananta.sfu-broadcast-capacity-derivation-gate.v1"
GATE_ID = "SFB-GATE-009"

SCALE_TIERS = (10, 25, 50, 100, 250)
CAPACITY_MODES = ("direct", "all_turn")
REQUIRED_GATE_EVIDENCE = (
    "SFB-BASE-007",
    "SFB-GATE-005",
    "SFB-GATE-006",
    "SFB-GATE-007",
    "SFB-GATE-008",
)
REQUIRED_DIGEST_KEYS = (
    "git_source_digest",
    "config_digest",
    "image_digest",
    "infrastructure_digest",
)

DEFAULT_RESERVE_PERCENT = 20.0
MINIMUM_PRODUCTION_RESERVE_PERCENT = 20.0
DEFAULT_MAXIMUM_VARIANCE_PERCENT = 15.0
MAXIMUM_PRODUCTION_VARIANCE_PERCENT = 15.0
MINIMUM_REPETITIONS = 3
MAXIMUM_INPUT_BYTES = 4 * 1024 * 1024
MAXIMUM_ENVIRONMENTS = 64
MAXIMUM_RECORDS_PER_ENVIRONMENT = 2048

_DIGEST_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_LABEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_NON_PRODUCTION_KEYS = frozenset(
    {"fixture", "test_fixture", "mock", "mocked", "synthetic", "simulated"}
)


class EvidenceError(ValueError):
    """Fail-closed evidence or derivation contract violation."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class VerifiedMeasurement:
    """A measurement after the production provenance boundary.

    Unit tests may construct this value to test arithmetic. Such values are not
    production evidence and cannot bypass ``derive_capacity_profiles``.
    """

    transport_mode: str
    receiver_tier: int
    passed: bool
    complete: bool
    repetitions: int
    variance_percent: float
    thresholds_passed: bool
    resource_limits_observed: bool
    security_controls_preserved: bool
    soak_stable: bool


def measurement_is_safe(
    measurement: VerifiedMeasurement,
    *,
    maximum_variance_percent: float,
) -> bool:
    """Return whether a verified aggregate is safe for capacity arithmetic."""

    _validate_non_negative_finite(maximum_variance_percent, "invalid_variance_limit")
    return bool(
        measurement.passed
        and measurement.complete
        and measurement.repetitions >= MINIMUM_REPETITIONS
        and math.isfinite(measurement.variance_percent)
        and 0.0 <= measurement.variance_percent <= maximum_variance_percent
        and measurement.thresholds_passed
        and measurement.resource_limits_observed
        and measurement.security_controls_preserved
        and measurement.soak_stable
    )


def contiguous_safe_tier(
    measurements: Sequence[VerifiedMeasurement],
    *,
    transport_mode: str,
    maximum_variance_percent: float,
) -> int | None:
    """Return the last contiguous passing tier, stopping at the first gap/fail."""

    if transport_mode not in CAPACITY_MODES:
        raise EvidenceError("unsupported_capacity_transport_mode")
    by_tier: dict[int, VerifiedMeasurement] = {}
    for measurement in measurements:
        if measurement.transport_mode != transport_mode:
            continue
        if measurement.receiver_tier not in SCALE_TIERS:
            raise EvidenceError("unsupported_scale_tier")
        if measurement.receiver_tier in by_tier:
            raise EvidenceError("duplicate_scale_tier_measurement")
        by_tier[measurement.receiver_tier] = measurement

    last_safe: int | None = None
    for tier in SCALE_TIERS:
        measurement = by_tier.get(tier)
        if measurement is None or not measurement_is_safe(
            measurement,
            maximum_variance_percent=maximum_variance_percent,
        ):
            break
        last_safe = tier
    return last_safe


def apply_conservative_reserve(receiver_tier: int, reserve_percent: float) -> int:
    """Floor a tested receiver tier after applying a non-negative reserve."""

    if isinstance(receiver_tier, bool) or not isinstance(receiver_tier, int):
        raise EvidenceError("invalid_receiver_tier")
    if receiver_tier <= 0:
        raise EvidenceError("invalid_receiver_tier")
    _validate_non_negative_finite(reserve_percent, "invalid_reserve_percent")
    if reserve_percent >= 100.0:
        raise EvidenceError("invalid_reserve_percent")
    retained = Decimal(receiver_tier) * (
        Decimal("1") - (Decimal(str(reserve_percent)) / Decimal("100"))
    )
    return int(retained.to_integral_value(rounding=ROUND_FLOOR))


def derive_candidate_from_verified_measurements(
    *,
    topology: str,
    infrastructure_profile: str,
    measurements: Sequence[VerifiedMeasurement],
    reserve_percent: float,
    maximum_variance_percent: float,
    evidence_digests: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Derive one unapproved candidate from already verified measurements."""

    direct_pair = [m for m in measurements if m.transport_mode == "direct_pair"]
    if len(direct_pair) != 1:
        raise EvidenceError("direct_pair_regression_missing_or_ambiguous")
    if not measurement_is_safe(
        direct_pair[0], maximum_variance_percent=maximum_variance_percent
    ):
        raise EvidenceError("direct_pair_regression_failed")

    direct_tier = contiguous_safe_tier(
        measurements,
        transport_mode="direct",
        maximum_variance_percent=maximum_variance_percent,
    )
    all_turn_tier = contiguous_safe_tier(
        measurements,
        transport_mode="all_turn",
        maximum_variance_percent=maximum_variance_percent,
    )
    if direct_tier is None:
        raise EvidenceError("direct_capacity_tier_missing")
    if all_turn_tier is None:
        raise EvidenceError("all_turn_capacity_tier_missing")

    direct_cap = apply_conservative_reserve(direct_tier, reserve_percent)
    all_turn_cap = apply_conservative_reserve(all_turn_tier, reserve_percent)
    admitted_cap = min(direct_cap, all_turn_cap)
    if admitted_cap < 1:
        raise EvidenceError("reserve_eliminates_capacity")

    candidate: dict[str, Any] = {
        "topology": topology,
        "infrastructure_profile": infrastructure_profile,
        "activation_state": "candidate_unapproved",
        "limits": {
            "admitted_receivers": admitted_cap,
            "receivers_by_transport": {
                "all_turn": all_turn_cap,
                "direct": direct_cap,
            },
            "verified_contiguous_tiers": {
                "all_turn": all_turn_tier,
                "direct": direct_tier,
            },
        },
        "derivation": {
            "direct_pair_is_regression_only": True,
            "direct_pair_used_for_capacity": False,
            "maximum_variance_percent": maximum_variance_percent,
            "reserve_percent": reserve_percent,
            "scale_tiers": list(SCALE_TIERS),
        },
    }
    if evidence_digests is not None:
        candidate["evidence_digests"] = dict(sorted(evidence_digests.items()))
    return candidate


def derive_capacity_profiles(
    bundle: Mapping[str, Any],
    *,
    reserve_percent: float = DEFAULT_RESERVE_PERCENT,
    maximum_variance_percent: float = DEFAULT_MAXIMUM_VARIANCE_PERCENT,
) -> dict[str, Any]:
    """Validate real evidence and return deterministic capacity candidates."""

    try:
        _validate_production_controls(reserve_percent, maximum_variance_percent)
        document = _require_mapping(bundle, "evidence_bundle_not_an_object")
        if _contains_non_production_marker(document):
            raise EvidenceError("non_production_evidence_rejected")
        if document.get("schema") != INPUT_SCHEMA:
            raise EvidenceError("unsupported_evidence_bundle_schema")
        policy = _require_mapping(
            document.get("attestation_policy", {}), "invalid_attestation_policy"
        )
        signature_required = policy.get("signature_required", False)
        if not isinstance(signature_required, bool):
            raise EvidenceError("invalid_signature_policy")
        environments = _require_list(
            document.get("environments"), "environments_missing"
        )
        if not environments:
            raise EvidenceError("verified_environment_evidence_missing")
        if len(environments) > MAXIMUM_ENVIRONMENTS:
            raise EvidenceError("environment_limit_exceeded")
    except EvidenceError as exc:
        return blocked_capacity_catalog(exc.reason_code)

    candidates: list[dict[str, Any]] = []
    blocked_environments: list[dict[str, Any]] = []
    seen_environments: set[tuple[str, str]] = set()

    for raw_environment in environments:
        topology = _safe_label(raw_environment, "topology")
        infrastructure_profile = _safe_label(
            raw_environment, "infrastructure_profile"
        )
        environment_key = (topology, infrastructure_profile)
        if environment_key in seen_environments:
            return blocked_capacity_catalog("duplicate_environment_evidence")
        seen_environments.add(environment_key)
        try:
            environment = _require_mapping(
                raw_environment, "environment_not_an_object"
            )
            _validate_label(topology, "invalid_topology")
            _validate_label(
                infrastructure_profile, "invalid_infrastructure_profile"
            )
            if _contains_non_production_marker(environment):
                raise EvidenceError("non_production_evidence_rejected")
            digests = _normalise_digests(environment.get("digests"))
            _validate_gate_evidence(
                environment.get("gate_evidence"),
                expected_digests=digests,
                signature_required=signature_required,
            )
            measurements = _parse_measurements(
                environment.get("measurements"),
                topology=topology,
                infrastructure_profile=infrastructure_profile,
                expected_digests=digests,
                signature_required=signature_required,
            )
            candidate = derive_candidate_from_verified_measurements(
                topology=topology,
                infrastructure_profile=infrastructure_profile,
                measurements=measurements,
                reserve_percent=reserve_percent,
                maximum_variance_percent=maximum_variance_percent,
                evidence_digests=digests,
            )
            candidates.append(candidate)
        except EvidenceError as exc:
            blocked_environments.append(
                {
                    "topology": topology,
                    "infrastructure_profile": infrastructure_profile,
                    "blocking_reasons": [exc.reason_code],
                }
            )

    candidates.sort(key=lambda item: (item["topology"], item["infrastructure_profile"]))
    blocked_environments.sort(
        key=lambda item: (item["topology"], item["infrastructure_profile"])
    )
    if not candidates:
        status = "blocked"
        blocking_reasons = ["no_derivable_capacity_candidate"]
    elif blocked_environments:
        status = "partial"
        blocking_reasons = ["one_or_more_environments_blocked"]
    else:
        status = "candidate"
        blocking_reasons = []

    return {
        "schema": OUTPUT_SCHEMA,
        "status": status,
        "activation": {
            "automatic": False,
            "performed": False,
            "state": "requires_admin_approval",
        },
        "derivation_policy": {
            "direct_pair_is_regression_only": True,
            "first_failed_or_missing_tier_stops": True,
            "maximum_variance_percent": maximum_variance_percent,
            "minimum_repetitions": MINIMUM_REPETITIONS,
            "required_gate_evidence": list(REQUIRED_GATE_EVIDENCE),
            "reserve_percent": reserve_percent,
        },
        "candidates": candidates,
        "blocked_environments": blocked_environments,
        "blocking_reasons": blocking_reasons,
    }


def blocked_capacity_catalog(*reason_codes: str) -> dict[str, Any]:
    """Build the deterministic, non-activating fail-closed output."""

    reasons = sorted(set(reason_codes)) or ["verified_real_evidence_missing"]
    return {
        "schema": OUTPUT_SCHEMA,
        "status": "blocked",
        "activation": {
            "automatic": False,
            "performed": False,
            "state": "requires_admin_approval",
        },
        "derivation_policy": {
            "direct_pair_is_regression_only": True,
            "first_failed_or_missing_tier_stops": True,
            "maximum_variance_percent": DEFAULT_MAXIMUM_VARIANCE_PERCENT,
            "minimum_repetitions": MINIMUM_REPETITIONS,
            "required_gate_evidence": list(REQUIRED_GATE_EVIDENCE),
            "reserve_percent": DEFAULT_RESERVE_PERCENT,
        },
        "candidates": [],
        "blocked_environments": [],
        "blocking_reasons": reasons,
    }


def build_gate_report(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Map the candidate catalog to the release gate's evidence report."""

    catalog_status = catalog.get("status")
    if catalog_status == "candidate":
        status = "passed"
    elif catalog_status == "partial":
        status = "partial"
    else:
        status = "blocked"
    reasons = catalog.get("blocking_reasons", [])
    if not isinstance(reasons, list):
        reasons = ["invalid_capacity_catalog"]
    candidates = catalog.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    return {
        "schema": GATE_REPORT_SCHEMA,
        "gate_id": GATE_ID,
        "status": status,
        "release_blocking": status != "passed",
        "activation_performed": False,
        "candidate_count": candidate_count,
        "claims": {
            "all_required_evidence_verified": status == "passed",
            "only_real_attested_executions_used": status == "passed",
            "capacity_profiles_activated": False,
        },
        "blocking_reasons": reasons,
    }


def read_bounded_json(path: Path) -> Mapping[str, Any]:
    """Read one regular, non-symlink JSON file with a hard byte limit."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError("evidence_bundle_unreadable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceError("evidence_bundle_not_regular_file")
        if metadata.st_size > MAXIMUM_INPUT_BYTES:
            raise EvidenceError("evidence_bundle_too_large")
        chunks: list[bytes] = []
        remaining = MAXIMUM_INPUT_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAXIMUM_INPUT_BYTES:
            raise EvidenceError("evidence_bundle_too_large")
    finally:
        os.close(descriptor)
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise EvidenceError("evidence_bundle_invalid_json") from exc
    return _require_mapping(parsed, "evidence_bundle_not_an_object")


def atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    """Write deterministic JSON without following an existing output symlink."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise EvidenceError("output_symlink_rejected")
    payload = json.dumps(
        document,
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_gate_evidence(
    value: Any,
    *,
    expected_digests: Mapping[str, str],
    signature_required: bool,
) -> None:
    records = _require_list(value, "gate_evidence_missing")
    if len(records) > MAXIMUM_RECORDS_PER_ENVIRONMENT:
        raise EvidenceError("gate_evidence_limit_exceeded")
    seen: set[str] = set()
    for raw_record in records:
        record = _require_mapping(raw_record, "gate_evidence_not_an_object")
        gate_id = record.get("gate_id")
        if not isinstance(gate_id, str):
            raise EvidenceError("gate_id_missing")
        if gate_id in seen:
            raise EvidenceError("duplicate_gate_evidence")
        seen.add(gate_id)
        _validate_real_record(
            record,
            expected_digests=expected_digests,
            signature_required=signature_required,
        )
        if record.get("status") != "passed" or record.get("complete") is not True:
            raise EvidenceError("required_gate_not_complete")
    missing = sorted(set(REQUIRED_GATE_EVIDENCE) - seen)
    if missing:
        raise EvidenceError("required_gate_evidence_missing")


def _parse_measurements(
    value: Any,
    *,
    topology: str,
    infrastructure_profile: str,
    expected_digests: Mapping[str, str],
    signature_required: bool,
) -> list[VerifiedMeasurement]:
    records = _require_list(value, "capacity_measurements_missing")
    if not records:
        raise EvidenceError("capacity_measurements_missing")
    if len(records) > MAXIMUM_RECORDS_PER_ENVIRONMENT:
        raise EvidenceError("capacity_measurement_limit_exceeded")
    parsed: list[VerifiedMeasurement] = []
    for raw_record in records:
        record = _require_mapping(raw_record, "capacity_measurement_not_an_object")
        _validate_real_record(
            record,
            expected_digests=expected_digests,
            signature_required=signature_required,
        )
        if record.get("topology") != topology:
            raise EvidenceError("measurement_topology_mismatch")
        if record.get("infrastructure_profile") != infrastructure_profile:
            raise EvidenceError("measurement_infrastructure_profile_mismatch")
        mode = record.get("transport_mode")
        if mode not in (*CAPACITY_MODES, "direct_pair"):
            raise EvidenceError("unsupported_measurement_transport_mode")
        tier = _require_integer(record.get("receiver_tier"), "invalid_receiver_tier")
        if tier <= 0 or (mode in CAPACITY_MODES and tier not in SCALE_TIERS):
            raise EvidenceError("unsupported_scale_tier")
        status_value = record.get("status")
        if status_value not in ("passed", "failed"):
            raise EvidenceError("invalid_measurement_status")
        quality = _require_mapping(
            record.get("quality"), "measurement_quality_missing"
        )
        parsed.append(
            VerifiedMeasurement(
                transport_mode=mode,
                receiver_tier=tier,
                passed=status_value == "passed",
                complete=_require_boolean(
                    record.get("complete"), "measurement_completeness_missing"
                ),
                repetitions=_require_integer(
                    record.get("repetitions"), "measurement_repetitions_missing"
                ),
                variance_percent=_require_number(
                    record.get("variance_percent"), "measurement_variance_missing"
                ),
                thresholds_passed=_require_boolean(
                    quality.get("thresholds_passed"), "threshold_result_missing"
                ),
                resource_limits_observed=_require_boolean(
                    quality.get("resource_limits_observed"),
                    "resource_limit_result_missing",
                ),
                security_controls_preserved=_require_boolean(
                    quality.get("security_controls_preserved"),
                    "security_result_missing",
                ),
                soak_stable=_require_boolean(
                    quality.get("soak_stable"), "soak_result_missing"
                ),
            )
        )
    return parsed


def _validate_real_record(
    record: Mapping[str, Any],
    *,
    expected_digests: Mapping[str, str],
    signature_required: bool,
) -> None:
    if _contains_non_production_marker(record):
        raise EvidenceError("non_production_evidence_rejected")
    execution = _require_mapping(record.get("execution"), "execution_attestation_missing")
    if (
        execution.get("kind") != "real"
        or execution.get("attested") is not True
        or execution.get("real_processes") is not True
    ):
        raise EvidenceError("real_execution_attestation_missing")
    verification = _require_mapping(
        record.get("verification"), "evidence_verification_missing"
    )
    if (
        verification.get("status") != "verified"
        or verification.get("complete") is not True
        or verification.get("fresh") is not True
        or verification.get("verifier") != "release_evidence_provenance"
    ):
        raise EvidenceError("evidence_not_verified_fresh_and_complete")
    artifact_digest = verification.get("artifact_digest")
    if not isinstance(artifact_digest, str) or _DIGEST_RE.fullmatch(artifact_digest) is None:
        raise EvidenceError("verified_artifact_digest_missing")
    actual_digests = _normalise_digests(verification.get("digests"))
    if actual_digests != dict(expected_digests):
        raise EvidenceError("evidence_digest_mismatch")
    if signature_required:
        signature = _require_mapping(
            verification.get("signature"), "required_signature_missing"
        )
        if signature.get("status") != "verified":
            raise EvidenceError("required_signature_not_verified")


def _normalise_digests(value: Any) -> dict[str, str]:
    digests = _require_mapping(value, "evidence_digests_missing")
    if set(digests) != set(REQUIRED_DIGEST_KEYS):
        raise EvidenceError("evidence_digest_set_mismatch")
    normalised: dict[str, str] = {}
    for key in REQUIRED_DIGEST_KEYS:
        raw_digest = digests.get(key)
        if not isinstance(raw_digest, str):
            raise EvidenceError("invalid_evidence_digest")
        match = _DIGEST_RE.fullmatch(raw_digest)
        if match is None:
            raise EvidenceError("invalid_evidence_digest")
        normalised[key] = match.group(1).lower()
    return normalised


def _contains_non_production_marker(value: Any) -> bool:
    stack = [value]
    visited = 0
    while stack:
        current = stack.pop()
        visited += 1
        if visited > 100_000:
            raise EvidenceError("evidence_structure_limit_exceeded")
        if isinstance(current, Mapping):
            for key, child in current.items():
                if str(key).casefold() in _NON_PRODUCTION_KEYS and _is_truthy_marker(child):
                    return True
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _is_truthy_marker(value: Any) -> bool:
    if value is False or value is None or value == 0:
        return False
    if isinstance(value, str) and value.strip().casefold() in {"", "false", "no", "none"}:
        return False
    return True


def _validate_production_controls(
    reserve_percent: float, maximum_variance_percent: float
) -> None:
    _validate_non_negative_finite(reserve_percent, "invalid_reserve_percent")
    if not MINIMUM_PRODUCTION_RESERVE_PERCENT <= reserve_percent < 100.0:
        raise EvidenceError("production_reserve_below_minimum")
    _validate_non_negative_finite(
        maximum_variance_percent, "invalid_variance_limit"
    )
    if maximum_variance_percent > MAXIMUM_PRODUCTION_VARIANCE_PERCENT:
        raise EvidenceError("production_variance_limit_too_high")


def _validate_non_negative_finite(value: float, reason_code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(reason_code)
    if not math.isfinite(float(value)) or float(value) < 0.0:
        raise EvidenceError(reason_code)


def _validate_label(value: str, reason_code: str) -> None:
    if _LABEL_RE.fullmatch(value) is None:
        raise EvidenceError(reason_code)


def _safe_label(value: Any, key: str) -> str:
    if isinstance(value, Mapping):
        candidate = value.get(key)
        if isinstance(candidate, str) and _LABEL_RE.fullmatch(candidate):
            return candidate
    return "invalid"


def _require_mapping(value: Any, reason_code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceError(reason_code)
    return value


def _require_list(value: Any, reason_code: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(reason_code)
    return value


def _require_boolean(value: Any, reason_code: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceError(reason_code)
    return value


def _require_integer(value: Any, reason_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(reason_code)
    return value


def _require_number(value: Any, reason_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(reason_code)
    converted = float(value)
    if not math.isfinite(converted) or converted < 0.0:
        raise EvidenceError(reason_code)
    return converted


def _parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=repository_root
        / "config/generated/sfu_broadcast_capacity_profiles.json",
    )
    parser.add_argument(
        "--gate-report-output",
        type=Path,
        default=repository_root
        / "artifacts/test-gates/sfu-broadcast-capacity-derivation.json",
    )
    parser.add_argument(
        "--reserve-percent", type=float, default=DEFAULT_RESERVE_PERCENT
    )
    parser.add_argument(
        "--maximum-variance-percent",
        type=float,
        default=DEFAULT_MAXIMUM_VARIANCE_PERCENT,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence = read_bounded_json(args.evidence)
        catalog = derive_capacity_profiles(
            evidence,
            reserve_percent=args.reserve_percent,
            maximum_variance_percent=args.maximum_variance_percent,
        )
    except EvidenceError as exc:
        catalog = blocked_capacity_catalog(exc.reason_code)
    gate_report = build_gate_report(catalog)
    try:
        atomic_write_json(args.output, catalog)
        atomic_write_json(args.gate_report_output, gate_report)
    except (EvidenceError, OSError, ValueError) as exc:
        sys.stderr.write(f"capacity derivation output failed: {exc}\n")
        return 2
    return 0 if gate_report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
