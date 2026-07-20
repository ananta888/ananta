"""Fail-closed, content-free evidence primitives for semantic-media release gates.

This module is deliberately evaluation-only.  It cannot enable a feature, grant
consent, mutate a Hub task or convert missing external evidence into a pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EVIDENCE_SCHEMA = "ananta.semantic-media-gate-evidence.v1"
GATE_STATUSES = frozenset({"passed", "failed", "unverified"})
BLOCKING_RISKS = frozenset({"critical", "high"})
FORBIDDEN_KEY_FRAGMENTS = frozenset(
    {
        "audio",
        "ciphertext",
        "content",
        "embedding",
        "feature_vector",
        "frame",
        "key_material",
        "local_path",
        "media",
        "password",
        "payload",
        "pixel",
        "prompt",
        "raw_text",
        "secret",
        "token_value",
        "transcript",
    }
)


class ProgramEvidenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class GateEvidence:
    gate_id: str
    status: str
    reason_codes: tuple[str, ...]
    source_sha256: str
    config_sha256: str
    measurements: Mapping[str, int | float | bool | str]
    evidence_kind: str = "automated"
    owner: str = "semantic-media-maintainers"
    expires_on: str | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        if self.status not in GATE_STATUSES:
            raise ProgramEvidenceError("gate_status_invalid")
        if not _safe_identifier(self.gate_id) or not _safe_identifier(self.evidence_kind):
            raise ProgramEvidenceError("gate_identifier_invalid")
        if not _digest(self.source_sha256) or not _digest(self.config_sha256):
            raise ProgramEvidenceError("gate_digest_invalid")
        if self.evidence_kind not in {"automated", "manual"}:
            raise ProgramEvidenceError("gate_evidence_kind_invalid")
        if self.evidence_kind == "manual" and not (self.owner and self.expires_on and self.rationale):
            raise ProgramEvidenceError("manual_evidence_incomplete")
        for reason in self.reason_codes:
            if not _safe_identifier(reason):
                raise ProgramEvidenceError("gate_reason_invalid")
        assert_content_free(self.measurements)

    @property
    def release_blocking(self) -> bool:
        return self.status != "passed"

    def as_document(self) -> dict[str, Any]:
        document = {"schema": EVIDENCE_SCHEMA, **asdict(self), "release_blocking": self.release_blocking}
        assert_content_free(document)
        return document


def unavailable_evidence(
    gate_id: str,
    *,
    source_sha256: str,
    config_sha256: str,
    reason_code: str = "external_evidence_unavailable",
) -> GateEvidence:
    return GateEvidence(
        gate_id=gate_id,
        status="unverified",
        reason_codes=(reason_code,),
        source_sha256=source_sha256,
        config_sha256=config_sha256,
        measurements={"verified_runs": 0},
    )


def source_hash(root: Path, relative_paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    normalized = sorted(set(relative_paths))
    if not normalized:
        raise ProgramEvidenceError("source_set_empty")
    for relative in normalized:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ProgramEvidenceError("source_path_unsafe")
        resolved = root / path
        if not resolved.is_file():
            raise ProgramEvidenceError("source_file_missing")
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_bound_report(
    report: Mapping[str, Any],
    *,
    expected_gate_id: str,
    expected_source_sha256: str,
    expected_config_sha256: str,
) -> GateEvidence:
    if set(report) != {
        "schema",
        "gate_id",
        "status",
        "reason_codes",
        "source_sha256",
        "config_sha256",
        "measurements",
        "evidence_kind",
        "owner",
        "expires_on",
        "rationale",
        "release_blocking",
    }:
        raise ProgramEvidenceError("gate_report_shape_invalid")
    if report.get("schema") != EVIDENCE_SCHEMA:
        raise ProgramEvidenceError("gate_report_version_invalid")
    if report.get("gate_id") != expected_gate_id:
        raise ProgramEvidenceError("gate_report_identity_mismatch")
    if report.get("source_sha256") != expected_source_sha256:
        raise ProgramEvidenceError("gate_report_source_stale")
    if report.get("config_sha256") != expected_config_sha256:
        raise ProgramEvidenceError("gate_report_config_stale")
    evidence = GateEvidence(
        gate_id=str(report["gate_id"]),
        status=str(report["status"]),
        reason_codes=tuple(report["reason_codes"]),
        source_sha256=str(report["source_sha256"]),
        config_sha256=str(report["config_sha256"]),
        measurements=dict(report["measurements"]),
        evidence_kind=str(report["evidence_kind"]),
        owner=str(report["owner"]),
        expires_on=report["expires_on"],
        rationale=report["rationale"],
    )
    if bool(report["release_blocking"]) != evidence.release_blocking:
        raise ProgramEvidenceError("gate_report_decision_inconsistent")
    return evidence


def write_report(path: Path, evidence: GateEvidence) -> None:
    """Atomically replace a content-free evidence file."""

    document = evidence.as_document()
    rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def assert_content_free(value: Any, *, known_secrets: Sequence[str] = (), path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ProgramEvidenceError(f"content_field_forbidden:{'.'.join((*path, str(key)))}")
            assert_content_free(nested, known_secrets=known_secrets, path=(*path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_content_free(nested, known_secrets=known_secrets, path=(*path, str(index)))
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ProgramEvidenceError("non_finite_evidence_value")
    if isinstance(value, str):
        if len(value) > 512 or any(secret and secret in value for secret in known_secrets):
            raise ProgramEvidenceError("secret_or_unbounded_evidence_value")
        if value.startswith(("/", "file:", "~")) or "\\" in value:
            raise ProgramEvidenceError("absolute_path_in_evidence")


def release_decision(evidence: Sequence[GateEvidence]) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        if item.gate_id in seen:
            reasons.append("duplicate_gate_evidence")
        seen.add(item.gate_id)
        if item.status != "passed":
            reasons.extend(item.reason_codes or ("gate_not_passed",))
    return ("go", ()) if not reasons else ("no_go", tuple(sorted(set(reasons))))


def _safe_identifier(value: str) -> bool:
    return bool(value) and len(value) <= 128 and all(character.isalnum() or character in "._:-" for character in value)


def _digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "BLOCKING_RISKS",
    "EVIDENCE_SCHEMA",
    "FORBIDDEN_KEY_FRAGMENTS",
    "GATE_STATUSES",
    "GateEvidence",
    "ProgramEvidenceError",
    "assert_content_free",
    "canonical_sha256",
    "release_decision",
    "source_hash",
    "unavailable_evidence",
    "verify_bound_report",
    "write_report",
]
