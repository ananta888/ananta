"""Pure, fail-closed provenance verification for release evidence.

Filesystem access and signature implementations are injected ports.  The
service neither parses CLI arguments nor persists manifests or reports.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

MANIFEST_SCHEMA = "ananta.sfu-broadcast-evidence-manifest.v1"
GATE_CONFIG_SCHEMA = "ananta.sfu-broadcast-gate-manifest.v1"
REPORT_SCHEMA = "ananta.sfu-broadcast-evidence-verification-report.v1"
PASSING_STATUS = "passed"
ENTRY_STATUSES = frozenset({"passed", "failed", "partial", "unverified"})
DIGEST_CATEGORIES = ("config", "lockfile", "image", "infrastructure")
DIGEST_FIELDS = {
    "config": "config_digests",
    "lockfile": "lockfile_digests",
    "image": "image_digests",
    "infrastructure": "infrastructure_digests",
}


class ReasonCode(str, Enum):
    MANIFEST_INPUT_INVALID = "MANIFEST_INPUT_INVALID"
    GATE_CONFIG_INPUT_INVALID = "GATE_CONFIG_INPUT_INVALID"
    VERIFIER_ARGUMENT_INVALID = "VERIFIER_ARGUMENT_INVALID"
    MANIFEST_SCHEMA_INVALID = "MANIFEST_SCHEMA_INVALID"
    MANIFEST_SHAPE_INVALID = "MANIFEST_SHAPE_INVALID"
    MANIFEST_ENTRIES_MISSING = "MANIFEST_ENTRIES_MISSING"
    MANIFEST_ENTRY_INVALID = "MANIFEST_ENTRY_INVALID"
    DUPLICATE_ARTIFACT_PATH = "DUPLICATE_ARTIFACT_PATH"
    GATE_CONFIG_SCHEMA_INVALID = "GATE_CONFIG_SCHEMA_INVALID"
    GATE_CONFIG_INVALID = "GATE_CONFIG_INVALID"
    GATE_CONFIG_DUPLICATE_GATE = "GATE_CONFIG_DUPLICATE_GATE"
    GATE_CONFIG_ATTESTATION_PROFILE_UNKNOWN = "GATE_CONFIG_ATTESTATION_PROFILE_UNKNOWN"
    GATE_UNKNOWN = "GATE_UNKNOWN"
    ARTIFACT_PATH_INVALID = "ARTIFACT_PATH_INVALID"
    ARTIFACT_PATH_OUTSIDE_ROOT = "ARTIFACT_PATH_OUTSIDE_ROOT"
    ARTIFACT_SYMLINK_FORBIDDEN = "ARTIFACT_SYMLINK_FORBIDDEN"
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    ARTIFACT_NOT_REGULAR_FILE = "ARTIFACT_NOT_REGULAR_FILE"
    ARTIFACT_UNREADABLE = "ARTIFACT_UNREADABLE"
    ARTIFACT_READ_FAILED = "ARTIFACT_READ_FAILED"
    ARTIFACT_DIGEST_INVALID = "ARTIFACT_DIGEST_INVALID"
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    ARTIFACT_JSON_INVALID = "ARTIFACT_JSON_INVALID"
    ARTIFACT_SCHEMA_UNSUPPORTED = "ARTIFACT_SCHEMA_UNSUPPORTED"
    ARTIFACT_SCHEMA_MISMATCH = "ARTIFACT_SCHEMA_MISMATCH"
    ARTIFACT_STATUS_MISMATCH = "ARTIFACT_STATUS_MISMATCH"
    ARTIFACT_STATUS_NOT_PASSED = "ARTIFACT_STATUS_NOT_PASSED"
    EVIDENCE_STATUS_INVALID = "EVIDENCE_STATUS_INVALID"
    EVIDENCE_STATUS_NOT_PASSED = "EVIDENCE_STATUS_NOT_PASSED"
    FRESHNESS_INVALID = "FRESHNESS_INVALID"
    FRESHNESS_WINDOW_INVALID = "FRESHNESS_WINDOW_INVALID"
    EVIDENCE_CLOCK_SKEW = "EVIDENCE_CLOCK_SKEW"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    GIT_SOURCE_DIGEST_INVALID = "GIT_SOURCE_DIGEST_INVALID"
    EXPECTED_GIT_SOURCE_DIGEST_INVALID = "EXPECTED_GIT_SOURCE_DIGEST_INVALID"
    GIT_SOURCE_DIGEST_MISMATCH = "GIT_SOURCE_DIGEST_MISMATCH"
    DIGEST_BINDING_INVALID = "DIGEST_BINDING_INVALID"
    EXPECTED_DIGEST_SET_INVALID = "EXPECTED_DIGEST_SET_INVALID"
    CONFIG_DIGEST_MISSING = "CONFIG_DIGEST_MISSING"
    CONFIG_DIGEST_MISMATCH = "CONFIG_DIGEST_MISMATCH"
    CONFIG_DIGEST_SET_MISMATCH = "CONFIG_DIGEST_SET_MISMATCH"
    EXPECTED_CONFIG_DIGEST_MISSING = "EXPECTED_CONFIG_DIGEST_MISSING"
    LOCKFILE_DIGEST_MISSING = "LOCKFILE_DIGEST_MISSING"
    LOCKFILE_DIGEST_MISMATCH = "LOCKFILE_DIGEST_MISMATCH"
    LOCKFILE_DIGEST_SET_MISMATCH = "LOCKFILE_DIGEST_SET_MISMATCH"
    EXPECTED_LOCKFILE_DIGEST_MISSING = "EXPECTED_LOCKFILE_DIGEST_MISSING"
    IMAGE_DIGEST_MISSING = "IMAGE_DIGEST_MISSING"
    IMAGE_DIGEST_MISMATCH = "IMAGE_DIGEST_MISMATCH"
    IMAGE_DIGEST_SET_MISMATCH = "IMAGE_DIGEST_SET_MISMATCH"
    EXPECTED_IMAGE_DIGEST_MISSING = "EXPECTED_IMAGE_DIGEST_MISSING"
    INFRASTRUCTURE_DIGEST_MISSING = "INFRASTRUCTURE_DIGEST_MISSING"
    INFRASTRUCTURE_DIGEST_MISMATCH = "INFRASTRUCTURE_DIGEST_MISMATCH"
    INFRASTRUCTURE_DIGEST_SET_MISMATCH = "INFRASTRUCTURE_DIGEST_SET_MISMATCH"
    EXPECTED_INFRASTRUCTURE_DIGEST_MISSING = "EXPECTED_INFRASTRUCTURE_DIGEST_MISSING"
    ATTESTATION_REQUIRED = "ATTESTATION_REQUIRED"
    ATTESTATION_PROFILE_MISMATCH = "ATTESTATION_PROFILE_MISMATCH"
    ATTESTATION_KEY_UNTRUSTED = "ATTESTATION_KEY_UNTRUSTED"
    ATTESTATION_VERIFIER_UNAVAILABLE = "ATTESTATION_VERIFIER_UNAVAILABLE"
    ATTESTATION_INVALID = "ATTESTATION_INVALID"


ACCESS_REASON_CODES = frozenset(
    {
        ReasonCode.ARTIFACT_PATH_INVALID.value,
        ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT.value,
        ReasonCode.ARTIFACT_SYMLINK_FORBIDDEN.value,
        ReasonCode.ARTIFACT_MISSING.value,
        ReasonCode.ARTIFACT_NOT_REGULAR_FILE.value,
        ReasonCode.ARTIFACT_UNREADABLE.value,
        ReasonCode.ARTIFACT_READ_FAILED.value,
    }
)


class ArtifactReadError(OSError):
    def __init__(self, reason_code: str | ReasonCode) -> None:
        value = reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
        super().__init__(value)
        self.reason_code = value


class ArtifactReader(Protocol):
    def read(self, repository_relative_path: str) -> bytes: ...


class AttestationVerifier(Protocol):
    def verify(self, payload: bytes, *, key_id: str, signature: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class VerificationContext:
    git_source_digest: str
    config_digests: Mapping[str, str] = field(default_factory=dict)
    lockfile_digests: Mapping[str, str] = field(default_factory=dict)
    image_digests: Mapping[str, str] = field(default_factory=dict)
    infrastructure_digests: Mapping[str, str] = field(default_factory=dict)

    def digests_for(self, category: str) -> Mapping[str, str]:
        return {
            "config": self.config_digests,
            "lockfile": self.lockfile_digests,
            "image": self.image_digests,
            "infrastructure": self.infrastructure_digests,
        }[category]


@dataclass(frozen=True, slots=True)
class EntryVerification:
    gate_id: str
    artifact_path: str
    status: str
    reason_codes: tuple[str, ...]
    attestation_status: str

    def as_document(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "attestation_status": self.attestation_status,
            "gate_id": self.gate_id,
            "reason_codes": list(self.reason_codes),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    status: str
    reason_codes: tuple[str, ...]
    entries: tuple[EntryVerification, ...]

    def as_document(self) -> dict[str, Any]:
        entries = sorted(self.entries, key=lambda item: (item.gate_id, item.artifact_path))
        return {
            "entries": [item.as_document() for item in entries],
            "reason_codes": list(self.reason_codes),
            "schema": REPORT_SCHEMA,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class _AttestationProfile:
    profile_id: str
    trusted_key_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class _GatePolicy:
    gate_id: str
    artifact_schemas: frozenset[str]
    required_digest_ids: Mapping[str, frozenset[str]]
    max_age_seconds: int
    max_future_skew_seconds: int
    attestation_profile: str | None


@dataclass(frozen=True, slots=True)
class _GateConfiguration:
    policies: Mapping[str, _GatePolicy]
    profiles: Mapping[str, _AttestationProfile]


class _ConfigurationError(ValueError):
    def __init__(self, reason_code: ReasonCode) -> None:
        super().__init__(reason_code.value)
        self.reason_code = reason_code.value


def failed_report(reason_code: str | ReasonCode) -> VerificationReport:
    value = reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
    return VerificationReport(status="failed", reason_codes=(value,), entries=())


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify_evidence_manifest(
    manifest: Mapping[str, Any],
    *,
    gate_configuration: Mapping[str, Any],
    context: VerificationContext,
    artifact_reader: ArtifactReader,
    now: datetime,
    attestation_verifiers: Mapping[str, AttestationVerifier] | None = None,
) -> VerificationReport:
    """Verify all entries without mutating evidence or external state."""

    try:
        configuration = _parse_gate_configuration(gate_configuration)
    except _ConfigurationError as exc:
        return failed_report(exc.reason_code)

    if not isinstance(manifest, Mapping):
        return failed_report(ReasonCode.MANIFEST_SHAPE_INVALID)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        return failed_report(ReasonCode.MANIFEST_SCHEMA_INVALID)
    if set(manifest) != {"schema", "entries"}:
        return failed_report(ReasonCode.MANIFEST_SHAPE_INVALID)
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        return failed_report(ReasonCode.MANIFEST_ENTRIES_MISSING)

    try:
        normalized_now = now.astimezone(UTC)
    except (AttributeError, ValueError):
        return failed_report(ReasonCode.VERIFIER_ARGUMENT_INVALID)

    global_reasons = _validate_context(context)
    paths = [entry.get("artifact_path") for entry in raw_entries if isinstance(entry, Mapping)]
    duplicates = {path for path, count in Counter(paths).items() if isinstance(path, str) and count > 1}
    if duplicates:
        global_reasons.append(ReasonCode.DUPLICATE_ARTIFACT_PATH.value)

    verifiers = attestation_verifiers or {}
    results = tuple(
        _verify_entry(
            raw_entry,
            configuration=configuration,
            context=context,
            artifact_reader=artifact_reader,
            now=normalized_now,
            duplicate_paths=duplicates,
            attestation_verifiers=verifiers,
        )
        for raw_entry in raw_entries
    )
    all_reasons = sorted(set(global_reasons).union(*(result.reason_codes for result in results)))
    return VerificationReport(
        status="passed" if not all_reasons else "failed",
        reason_codes=tuple(all_reasons),
        entries=results,
    )


def _verify_entry(
    raw_entry: Any,
    *,
    configuration: _GateConfiguration,
    context: VerificationContext,
    artifact_reader: ArtifactReader,
    now: datetime,
    duplicate_paths: set[str],
    attestation_verifiers: Mapping[str, AttestationVerifier],
) -> EntryVerification:
    gate_id = str(raw_entry.get("gate_id") or "") if isinstance(raw_entry, Mapping) else ""
    artifact_path = str(raw_entry.get("artifact_path") or "") if isinstance(raw_entry, Mapping) else ""
    reasons = _entry_shape_reasons(raw_entry)
    attestation_status = "not_required"
    if artifact_path in duplicate_paths:
        reasons.append(ReasonCode.DUPLICATE_ARTIFACT_PATH.value)
    if reasons:
        return _entry_result(gate_id, artifact_path, reasons, attestation_status)

    entry = raw_entry
    policy = configuration.policies.get(gate_id)
    if policy is None:
        reasons.append(ReasonCode.GATE_UNKNOWN.value)
        return _entry_result(gate_id, artifact_path, reasons, attestation_status)

    if entry["artifact_schema"] not in policy.artifact_schemas:
        reasons.append(ReasonCode.ARTIFACT_SCHEMA_UNSUPPORTED.value)
    if entry["git_source_digest"] != context.git_source_digest:
        reasons.append(ReasonCode.GIT_SOURCE_DIGEST_MISMATCH.value)
    if entry["status"] != PASSING_STATUS:
        reasons.append(ReasonCode.EVIDENCE_STATUS_NOT_PASSED.value)

    for category in DIGEST_CATEGORIES:
        actual, binding_reasons = _digest_bindings(entry[DIGEST_FIELDS[category]])
        reasons.extend(binding_reasons)
        if actual is not None:
            reasons.extend(
                _compare_digest_bindings(
                    category,
                    actual=actual,
                    expected=context.digests_for(category),
                    required=policy.required_digest_ids[category],
                )
            )

    reasons.extend(_freshness_reasons(entry["freshness"], policy=policy, now=now))

    try:
        artifact = artifact_reader.read(artifact_path)
    except ArtifactReadError as exc:
        reason = exc.reason_code if exc.reason_code in ACCESS_REASON_CODES else ReasonCode.ARTIFACT_READ_FAILED.value
        reasons.append(reason)
    except OSError:
        reasons.append(ReasonCode.ARTIFACT_READ_FAILED.value)
    else:
        if sha256_bytes(artifact) != entry["artifact_sha256"]:
            reasons.append(ReasonCode.ARTIFACT_DIGEST_MISMATCH.value)
        try:
            artifact_document = json.loads(artifact.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reasons.append(ReasonCode.ARTIFACT_JSON_INVALID.value)
        else:
            if not isinstance(artifact_document, Mapping):
                reasons.append(ReasonCode.ARTIFACT_JSON_INVALID.value)
            else:
                if artifact_document.get("schema") != entry["artifact_schema"]:
                    reasons.append(ReasonCode.ARTIFACT_SCHEMA_MISMATCH.value)
                artifact_status = artifact_document.get("status")
                if artifact_status is not None and artifact_status != entry["status"]:
                    reasons.append(ReasonCode.ARTIFACT_STATUS_MISMATCH.value)
                if artifact_status is not None and artifact_status != PASSING_STATUS:
                    reasons.append(ReasonCode.ARTIFACT_STATUS_NOT_PASSED.value)

    if policy.attestation_profile is not None:
        attestation_status = "failed"
        profile = configuration.profiles[policy.attestation_profile]
        attestation = entry.get("attestation")
        if not isinstance(attestation, Mapping) or set(attestation) != {"profile_id", "key_id", "signature"}:
            reasons.append(ReasonCode.ATTESTATION_REQUIRED.value)
        elif attestation.get("profile_id") != profile.profile_id:
            reasons.append(ReasonCode.ATTESTATION_PROFILE_MISMATCH.value)
        elif attestation.get("key_id") not in profile.trusted_key_ids:
            reasons.append(ReasonCode.ATTESTATION_KEY_UNTRUSTED.value)
        else:
            verifier = attestation_verifiers.get(profile.profile_id)
            if verifier is None:
                reasons.append(ReasonCode.ATTESTATION_VERIFIER_UNAVAILABLE.value)
            else:
                unsigned_entry = {key: value for key, value in entry.items() if key != "attestation"}
                try:
                    verified = verifier.verify(
                        canonical_json_bytes(unsigned_entry),
                        key_id=str(attestation["key_id"]),
                        signature=str(attestation["signature"]),
                    )
                except Exception:
                    verified = False
                if verified:
                    attestation_status = "verified"
                else:
                    reasons.append(ReasonCode.ATTESTATION_INVALID.value)

    return _entry_result(gate_id, artifact_path, reasons, attestation_status)


def _entry_result(
    gate_id: str,
    artifact_path: str,
    reasons: Sequence[str],
    attestation_status: str,
) -> EntryVerification:
    normalized = tuple(sorted(set(reasons)))
    return EntryVerification(
        gate_id=gate_id,
        artifact_path=artifact_path,
        status="failed" if normalized else "passed",
        reason_codes=normalized,
        attestation_status=attestation_status,
    )


def _entry_shape_reasons(entry: Any) -> list[str]:
    if not isinstance(entry, Mapping):
        return [ReasonCode.MANIFEST_ENTRY_INVALID.value]
    required = {
        "gate_id",
        "artifact_schema",
        "artifact_path",
        "artifact_sha256",
        "git_source_digest",
        "config_digests",
        "lockfile_digests",
        "image_digests",
        "infrastructure_digests",
        "producer_command",
        "freshness",
        "status",
    }
    if not required.issubset(entry) or not set(entry).issubset(required | {"attestation"}):
        return [ReasonCode.MANIFEST_ENTRY_INVALID.value]
    reasons: list[str] = []
    if not _identifier(entry["gate_id"]) or not _identifier(entry["artifact_schema"]):
        reasons.append(ReasonCode.MANIFEST_ENTRY_INVALID.value)
    if not _repository_path(entry["artifact_path"]):
        reasons.append(ReasonCode.ARTIFACT_PATH_INVALID.value)
    if not _sha256(entry["artifact_sha256"]):
        reasons.append(ReasonCode.ARTIFACT_DIGEST_INVALID.value)
    if not _git_digest(entry["git_source_digest"]):
        reasons.append(ReasonCode.GIT_SOURCE_DIGEST_INVALID.value)
    if entry["status"] not in ENTRY_STATUSES:
        reasons.append(ReasonCode.EVIDENCE_STATUS_INVALID.value)
    if not isinstance(entry["producer_command"], str) or not 1 <= len(entry["producer_command"]) <= 2048:
        reasons.append(ReasonCode.MANIFEST_ENTRY_INVALID.value)
    if not isinstance(entry["freshness"], Mapping):
        reasons.append(ReasonCode.FRESHNESS_INVALID.value)
    for field_name in DIGEST_FIELDS.values():
        if not isinstance(entry[field_name], list):
            reasons.append(ReasonCode.DIGEST_BINDING_INVALID.value)
    return reasons


def _freshness_reasons(value: Mapping[str, Any], *, policy: _GatePolicy, now: datetime) -> list[str]:
    if set(value) != {"produced_at", "expires_at"}:
        return [ReasonCode.FRESHNESS_INVALID.value]
    produced_at = _parse_datetime(value.get("produced_at"))
    expires_at = _parse_datetime(value.get("expires_at"))
    if produced_at is None or expires_at is None:
        return [ReasonCode.FRESHNESS_INVALID.value]
    reasons: list[str] = []
    if expires_at <= produced_at:
        reasons.append(ReasonCode.FRESHNESS_WINDOW_INVALID.value)
    if produced_at > now + timedelta(seconds=policy.max_future_skew_seconds):
        reasons.append(ReasonCode.EVIDENCE_CLOCK_SKEW.value)
    if now > expires_at or now - produced_at > timedelta(seconds=policy.max_age_seconds):
        reasons.append(ReasonCode.EVIDENCE_STALE.value)
    return reasons


def _digest_bindings(value: Any) -> tuple[dict[str, str] | None, list[str]]:
    if not isinstance(value, list):
        return None, [ReasonCode.DIGEST_BINDING_INVALID.value]
    bindings: dict[str, str] = {}
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"id", "sha256"}
            or not _identifier(item.get("id"))
            or not _sha256(item.get("sha256"))
            or item["id"] in bindings
        ):
            return None, [ReasonCode.DIGEST_BINDING_INVALID.value]
        bindings[str(item["id"])] = str(item["sha256"])
    return bindings, []


def _compare_digest_bindings(
    category: str,
    *,
    actual: Mapping[str, str],
    expected: Mapping[str, str],
    required: frozenset[str],
) -> list[str]:
    prefix = category.upper()
    reasons: list[str] = []
    for identifier in required:
        if identifier not in expected:
            reasons.append(getattr(ReasonCode, f"EXPECTED_{prefix}_DIGEST_MISSING").value)
        if identifier not in actual:
            reasons.append(getattr(ReasonCode, f"{prefix}_DIGEST_MISSING").value)
    if set(actual) != set(expected):
        reasons.append(getattr(ReasonCode, f"{prefix}_DIGEST_SET_MISMATCH").value)
    if any(actual[key] != expected[key] for key in set(actual) & set(expected)):
        reasons.append(getattr(ReasonCode, f"{prefix}_DIGEST_MISMATCH").value)
    return reasons


def _validate_context(context: VerificationContext) -> list[str]:
    reasons: list[str] = []
    if not _git_digest(context.git_source_digest):
        reasons.append(ReasonCode.EXPECTED_GIT_SOURCE_DIGEST_INVALID.value)
    for category in DIGEST_CATEGORIES:
        digests = context.digests_for(category)
        if not isinstance(digests, Mapping) or any(not _identifier(key) or not _sha256(value) for key, value in digests.items()):
            reasons.append(ReasonCode.EXPECTED_DIGEST_SET_INVALID.value)
    return reasons


def _parse_gate_configuration(document: Mapping[str, Any]) -> _GateConfiguration:
    if not isinstance(document, Mapping):
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    if document.get("schema") != GATE_CONFIG_SCHEMA:
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_SCHEMA_INVALID)
    required = {
        "schema",
        "evidence_manifest_schema",
        "artifact_root",
        "default_policy",
        "attestation_profiles",
        "gates",
    }
    if set(document) != required or document.get("evidence_manifest_schema") != MANIFEST_SCHEMA:
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    if not _repository_path(document.get("artifact_root")):
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    default = document.get("default_policy")
    if not isinstance(default, Mapping) or set(default) != {"max_age_seconds", "max_future_skew_seconds"}:
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    max_age = default.get("max_age_seconds")
    max_skew = default.get("max_future_skew_seconds")
    if type(max_age) is not int or type(max_skew) is not int or max_age < 1 or not 0 <= max_skew <= 3600:
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)

    raw_profiles = document.get("attestation_profiles")
    if not isinstance(raw_profiles, Mapping):
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    profiles: dict[str, _AttestationProfile] = {}
    for profile_id, raw_profile in raw_profiles.items():
        if not _identifier(profile_id) or not isinstance(raw_profile, Mapping):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        if set(raw_profile) != {"algorithm", "trusted_keys"} or not _identifier(raw_profile.get("algorithm")):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        raw_keys = raw_profile.get("trusted_keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        key_ids: set[str] = set()
        for key in raw_keys:
            if (
                not isinstance(key, Mapping)
                or set(key) != {"key_id", "public_key_path", "public_key_sha256"}
                or not _identifier(key.get("key_id"))
                or not _repository_path(key.get("public_key_path"))
                or not _sha256(key.get("public_key_sha256"))
                or key["key_id"] in key_ids
            ):
                raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
            key_ids.add(str(key["key_id"]))
        profiles[str(profile_id)] = _AttestationProfile(str(profile_id), frozenset(key_ids))

    raw_gates = document.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
    policies: dict[str, _GatePolicy] = {}
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, Mapping):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        allowed = {
            "gate_id",
            "artifact_schemas",
            "required_digest_ids",
            "attestation_profile",
            "max_age_seconds",
            "max_future_skew_seconds",
        }
        mandatory = {"gate_id", "artifact_schemas", "required_digest_ids", "attestation_profile"}
        if not mandatory.issubset(raw_gate) or not set(raw_gate).issubset(allowed):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        gate_id = raw_gate.get("gate_id")
        schemas = raw_gate.get("artifact_schemas")
        required_ids = raw_gate.get("required_digest_ids")
        if not _identifier(gate_id) or not isinstance(schemas, list) or not schemas:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        if len(set(schemas)) != len(schemas) or any(not _identifier(schema) for schema in schemas):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        if gate_id in policies:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_DUPLICATE_GATE)
        if not isinstance(required_ids, Mapping) or set(required_ids) != set(DIGEST_CATEGORIES):
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        normalized_required: dict[str, frozenset[str]] = {}
        for category in DIGEST_CATEGORIES:
            identifiers = required_ids[category]
            if not isinstance(identifiers, list) or len(set(identifiers)) != len(identifiers):
                raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
            if any(not _identifier(identifier) for identifier in identifiers):
                raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
            normalized_required[category] = frozenset(identifiers)
        profile_id = raw_gate.get("attestation_profile")
        if profile_id is not None and profile_id not in profiles:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_ATTESTATION_PROFILE_UNKNOWN)
        gate_max_age = raw_gate.get("max_age_seconds", max_age)
        gate_max_skew = raw_gate.get("max_future_skew_seconds", max_skew)
        if type(gate_max_age) is not int or type(gate_max_skew) is not int:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        if gate_max_age < 1 or not 0 <= gate_max_skew <= 3600:
            raise _ConfigurationError(ReasonCode.GATE_CONFIG_INVALID)
        policies[str(gate_id)] = _GatePolicy(
            gate_id=str(gate_id),
            artifact_schemas=frozenset(str(schema) for schema in schemas),
            required_digest_ids=normalized_required,
            max_age_seconds=gate_max_age,
            max_future_skew_seconds=gate_max_skew,
            attestation_profile=str(profile_id) if profile_id is not None else None,
        )
    return _GateConfiguration(policies=policies, profiles=profiles)


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is not None


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _git_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value) is not None


def _repository_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
        return False
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        return False
    return not PurePosixPath(value).is_absolute()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


__all__ = [
    "ACCESS_REASON_CODES",
    "ArtifactReadError",
    "ArtifactReader",
    "AttestationVerifier",
    "EntryVerification",
    "GATE_CONFIG_SCHEMA",
    "MANIFEST_SCHEMA",
    "REPORT_SCHEMA",
    "ReasonCode",
    "VerificationContext",
    "VerificationReport",
    "canonical_json_bytes",
    "failed_report",
    "sha256_bytes",
    "verify_evidence_manifest",
]
