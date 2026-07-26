"""Closed transport contract for Recovery Worker artifact publication.

The contract carries metadata only.  Artifact bytes remain in the explicitly
mounted, task-scoped workspace until the Hub has authenticated the Worker,
revalidated the dispatch lease, read the file itself, and materialized a
Hub-owned immutable copy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

RECOVERY_ARTIFACT_INGRESS_SCHEMA = (
    "ananta.recovery_artifact_ingress.v1"
)
RECOVERY_ARTIFACT_RECEIPTS_SCHEMA = (
    "ananta.recovery_artifact_receipts.v1"
)
MAX_RECOVERY_ARTIFACT_COUNT = 32
MAX_RECOVERY_ARTIFACT_BYTES = 25 * 1024 * 1024
MAX_RECOVERY_ARTIFACT_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES = 262_144
MAX_RECOVERY_FORWARD_RESPONSE_BYTES = 1_048_576
_RECEIPT_FIXED_BUDGET_BYTES = 1_024

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$"
)
_KIND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_MEDIA_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+/-]{0,126}$"
)
_ARTIFACT_ID = re.compile(r"^recovery-artifact-[0-9a-f]{32}$")
_ARTIFACT_VERSION_ID = re.compile(
    r"^recovery-artifact-version-[0-9a-f]{32}$"
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "phase",
        "worker_url",
        "assignment_fingerprint",
        "request_fingerprint",
        "lease_token_digest",
        "artifacts",
        "digest",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "source_index",
        "kind",
        "workspace_path",
        "relative_path",
        "filename",
        "media_type",
        "size_bytes",
        "sha256",
        "worker_artifact_id",
        "worker_artifact_version_id",
    }
)
RECOVERY_ARTIFACT_RECEIPT_FIELDS = frozenset(
    {
        "kind",
        "task_id",
        "artifact_id",
        "artifact_version_id",
        "filename",
        "media_type",
        "workspace_relative_path",
        "content_hash",
        "size_bytes",
        "provenance_summary",
    }
)
_RECEIPT_PROVENANCE_FIELDS = frozenset(
    {
        "schema",
        "authority",
        "ingress",
        "worker_url",
        "manifest_digest",
        "source_index",
    }
)
_RECEIPTS_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "manifest_digest",
        "artifacts",
        "replayed",
    }
)


class RecoveryArtifactIngressContractError(ValueError):
    """Raised when artifact ingress metadata is malformed or unbound."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def recovery_artifact_assignment_fingerprint(
    *,
    task_id: str,
    worker_url: str,
) -> str:
    normalized_task_id = _identifier(
        task_id,
        "recovery_artifact_task_id_invalid",
    )
    normalized_worker_url = _worker_url(worker_url)
    return hashlib.sha256(
        (
            f"{normalized_task_id}\0{normalized_worker_url}"
        ).encode("utf-8")
    ).hexdigest()


def recovery_artifact_lease_token_digest(token: str) -> str:
    value = str(token or "")
    if not value or len(value.encode("utf-8")) > 16_384:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_lease_token_invalid"
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_recovery_artifact_ingress_manifest(
    *,
    task_id: str,
    worker_url: str,
    request_fingerprint: str,
    lease_token: str,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": RECOVERY_ARTIFACT_INGRESS_SCHEMA,
        "task_id": str(task_id or ""),
        "phase": "execute",
        "worker_url": str(worker_url or ""),
        "assignment_fingerprint": (
            recovery_artifact_assignment_fingerprint(
                task_id=task_id,
                worker_url=worker_url,
            )
        ),
        "request_fingerprint": str(
            request_fingerprint or ""
        ),
        "lease_token_digest": (
            recovery_artifact_lease_token_digest(
                lease_token
            )
        ),
        "artifacts": [dict(value) for value in artifacts],
    }
    payload["digest"] = _payload_digest(payload)
    return validate_recovery_artifact_ingress_manifest(
        payload,
        task_id=task_id,
        worker_url=worker_url,
        request_fingerprint=request_fingerprint,
        lease_token=lease_token,
    )


def validate_recovery_artifact_ingress_manifest(
    value: object,
    *,
    task_id: str | None = None,
    worker_url: str | None = None,
    request_fingerprint: str | None = None,
    lease_token: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_invalid"
        )
    raw = dict(value)
    if set(raw) != _MANIFEST_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_fields_invalid"
        )
    if raw.get("schema") != RECOVERY_ARTIFACT_INGRESS_SCHEMA:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_schema_invalid"
        )
    normalized_task_id = _identifier(
        raw.get("task_id"),
        "recovery_artifact_task_id_invalid",
    )
    if raw.get("phase") != "execute":
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_phase_invalid"
        )
    normalized_worker_url = _worker_url(raw.get("worker_url"))
    assignment = _digest(
        raw.get("assignment_fingerprint"),
        "recovery_artifact_assignment_invalid",
    )
    expected_assignment = (
        recovery_artifact_assignment_fingerprint(
            task_id=normalized_task_id,
            worker_url=normalized_worker_url,
        )
    )
    if not hmac.compare_digest(assignment, expected_assignment):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_assignment_mismatch"
        )
    fingerprint = _digest(
        raw.get("request_fingerprint"),
        "recovery_artifact_request_fingerprint_invalid",
    )
    token_digest = _digest(
        raw.get("lease_token_digest"),
        "recovery_artifact_lease_binding_invalid",
    )
    raw_artifacts = raw.get("artifacts")
    if (
        not isinstance(raw_artifacts, Sequence)
        or isinstance(raw_artifacts, (str, bytes, bytearray))
        or not raw_artifacts
        or len(raw_artifacts) > MAX_RECOVERY_ARTIFACT_COUNT
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_count_invalid"
        )
    artifacts = [
        _artifact(value, expected_index=index)
        for index, value in enumerate(raw_artifacts)
    ]
    total_bytes = sum(
        int(value["size_bytes"]) for value in artifacts
    )
    if total_bytes > MAX_RECOVERY_ARTIFACT_TOTAL_BYTES:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_total_size_exceeded"
        )

    expected_digest = _payload_digest(raw)
    actual_digest = _digest(
        raw.get("digest"),
        "recovery_artifact_manifest_digest_invalid",
    )
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_digest_mismatch"
        )
    if task_id is not None and not hmac.compare_digest(
        normalized_task_id,
        str(task_id or ""),
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_task_mismatch"
        )
    if worker_url is not None and not hmac.compare_digest(
        normalized_worker_url,
        _worker_url(worker_url),
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_worker_mismatch"
        )
    if request_fingerprint is not None and not hmac.compare_digest(
        fingerprint,
        str(request_fingerprint or ""),
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_request_fingerprint_mismatch"
        )
    if lease_token is not None and not hmac.compare_digest(
        token_digest,
        recovery_artifact_lease_token_digest(lease_token),
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_lease_binding_mismatch"
        )
    return {
        "schema": RECOVERY_ARTIFACT_INGRESS_SCHEMA,
        "task_id": normalized_task_id,
        "phase": "execute",
        "worker_url": normalized_worker_url,
        "assignment_fingerprint": assignment,
        "request_fingerprint": fingerprint,
        "lease_token_digest": token_digest,
        "artifacts": artifacts,
        "digest": actual_digest,
    }


def validate_recovery_artifact_receipt_list(
    value: object,
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    """Validate the closed, bounded receipt list carried by a Worker result."""

    if not isinstance(value, list):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_list_invalid"
        )
    if len(value) > MAX_RECOVERY_ARTIFACT_COUNT:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_count_exceeded"
        )
    expected_task_id = _identifier(
        task_id,
        "recovery_artifact_receipt_task_id_invalid",
    )
    remaining_budget = MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES
    receipts: list[dict[str, Any]] = []
    for index, receipt in enumerate(value):
        remaining_budget = _preflight_receipt(
            receipt,
            remaining_budget=remaining_budget,
        )
        receipts.append(
            _receipt(
                receipt,
                expected_task_id=expected_task_id,
                expected_index=index,
            )
        )
    if (
        sum(int(receipt["size_bytes"]) for receipt in receipts)
        > MAX_RECOVERY_ARTIFACT_TOTAL_BYTES
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_total_size_exceeded"
        )
    manifest_digests = {
        str(receipt["provenance_summary"]["manifest_digest"])
        for receipt in receipts
    }
    if len(manifest_digests) > 1:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_manifest_mismatch"
        )
    # Only normalized, closed, scalar-bounded values reach canonical JSON.
    _bounded_receipt_json(receipts)
    return receipts


def validate_recovery_artifact_receipts_payload(
    value: object,
    *,
    manifest: Mapping[str, Any],
    descriptors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one Hub response against the exact submitted manifest."""

    if not isinstance(value, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipts_payload_invalid"
        )
    raw = dict(value)
    if set(raw) != _RECEIPTS_PAYLOAD_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipts_payload_fields_invalid"
        )
    task_id = str(manifest.get("task_id") or "")
    manifest_digest = str(manifest.get("digest") or "")
    if (
        _bounded_text(
            raw.get("schema"),
            maximum_characters=64,
            reason_code=(
                "recovery_artifact_receipts_payload_binding_invalid"
            ),
        )
        != RECOVERY_ARTIFACT_RECEIPTS_SCHEMA
        or _identifier(
            raw.get("task_id"),
            "recovery_artifact_receipts_payload_binding_invalid",
        )
        != task_id
        or _digest(
            raw.get("manifest_digest"),
            "recovery_artifact_receipts_payload_binding_invalid",
        )
        != manifest_digest
        or not isinstance(raw.get("replayed"), bool)
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipts_payload_binding_invalid"
        )
    receipts = validate_recovery_artifact_receipt_list(
        raw.get("artifacts"),
        task_id=task_id,
    )
    expected_descriptors = [dict(value) for value in descriptors]
    if len(receipts) != len(expected_descriptors):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_count_mismatch"
        )
    for receipt, descriptor in zip(
        receipts,
        expected_descriptors,
        strict=True,
    ):
        if (
            receipt["kind"] != descriptor.get("kind")
            or receipt["filename"] != descriptor.get("filename")
            or receipt["media_type"] != descriptor.get("media_type")
            or receipt["workspace_relative_path"]
            != descriptor.get("relative_path")
            or receipt["content_hash"] != descriptor.get("sha256")
            or receipt["size_bytes"] != descriptor.get("size_bytes")
            or receipt["provenance_summary"]["manifest_digest"]
            != manifest_digest
            or receipt["provenance_summary"]["source_index"]
            != descriptor.get("source_index")
        ):
            raise RecoveryArtifactIngressContractError(
                "recovery_artifact_receipt_descriptor_mismatch"
            )
    normalized = {
        "schema": RECOVERY_ARTIFACT_RECEIPTS_SCHEMA,
        "task_id": task_id,
        "manifest_digest": manifest_digest,
        "artifacts": receipts,
        "replayed": raw["replayed"],
    }
    _bounded_receipt_json(normalized)
    return normalized


def _preflight_receipt(
    value: object,
    *,
    remaining_budget: int,
) -> int:
    """Reject open or oversized scalars before canonical serialization."""

    if not isinstance(value, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_invalid"
        )
    raw = dict(value)
    if set(raw) != RECOVERY_ARTIFACT_RECEIPT_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_fields_invalid"
        )
    provenance = raw.get("provenance_summary")
    if not isinstance(provenance, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_provenance_invalid"
        )
    normalized_provenance = dict(provenance)
    if set(normalized_provenance) != _RECEIPT_PROVENANCE_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_provenance_invalid"
        )
    remaining = remaining_budget - _RECEIPT_FIXED_BUDGET_BYTES
    scalar_limits = (
        (raw.get("kind"), 96),
        (raw.get("task_id"), 256),
        (raw.get("artifact_id"), 96),
        (raw.get("artifact_version_id"), 96),
        (raw.get("filename"), 255),
        (raw.get("media_type"), 127),
        (raw.get("workspace_relative_path"), 512),
        (raw.get("content_hash"), 64),
        (normalized_provenance.get("schema"), 64),
        (normalized_provenance.get("authority"), 16),
        (normalized_provenance.get("ingress"), 16),
        (normalized_provenance.get("worker_url"), 2_048),
        (normalized_provenance.get("manifest_digest"), 64),
    )
    for scalar, maximum_characters in scalar_limits:
        text = _bounded_text(
            scalar,
            maximum_characters=maximum_characters,
            reason_code="recovery_artifact_receipt_scalar_invalid",
        )
        remaining -= len(text.encode("utf-8"))
        if remaining < 0:
            raise RecoveryArtifactIngressContractError(
                "recovery_artifact_receipt_payload_too_large"
            )
    if (
        isinstance(raw.get("size_bytes"), bool)
        or not isinstance(raw.get("size_bytes"), int)
        or isinstance(
            normalized_provenance.get("source_index"),
            bool,
        )
        or not isinstance(
            normalized_provenance.get("source_index"),
            int,
        )
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_scalar_invalid"
        )
    return remaining


def _receipt(
    value: object,
    *,
    expected_task_id: str,
    expected_index: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_invalid"
        )
    raw = dict(value)
    if set(raw) != RECOVERY_ARTIFACT_RECEIPT_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_fields_invalid"
        )
    receipt_task_id = _identifier(
        raw.get("task_id"),
        "recovery_artifact_receipt_task_id_invalid",
    )
    size_bytes = raw.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > MAX_RECOVERY_ARTIFACT_BYTES
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_size_invalid"
        )
    provenance = raw.get("provenance_summary")
    if not isinstance(provenance, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_provenance_invalid"
        )
    normalized_provenance = dict(provenance)
    source_index = normalized_provenance.get("source_index")
    if (
        set(normalized_provenance) != _RECEIPT_PROVENANCE_FIELDS
        or normalized_provenance.get("schema")
        != "ananta.recovery_artifact_provenance.v1"
        or normalized_provenance.get("authority") != "hub"
        or normalized_provenance.get("ingress") != "workspace"
        or isinstance(source_index, bool)
        or not isinstance(source_index, int)
        or source_index != expected_index
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_provenance_invalid"
        )
    if receipt_task_id != expected_task_id:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_task_mismatch"
        )
    return {
        "kind": _pattern(
            raw.get("kind"),
            _KIND,
            "recovery_artifact_receipt_kind_invalid",
        ),
        "task_id": receipt_task_id,
        "artifact_id": _pattern(
            raw.get("artifact_id"),
            _ARTIFACT_ID,
            "recovery_artifact_receipt_id_invalid",
        ),
        "artifact_version_id": _pattern(
            raw.get("artifact_version_id"),
            _ARTIFACT_VERSION_ID,
            "recovery_artifact_receipt_version_id_invalid",
        ),
        "filename": _filename(raw.get("filename")),
        "media_type": _pattern(
            raw.get("media_type"),
            _MEDIA_TYPE,
            "recovery_artifact_receipt_media_type_invalid",
        ),
        "workspace_relative_path": _relative_path(
            raw.get("workspace_relative_path"),
            "recovery_artifact_receipt_path_invalid",
        ),
        "content_hash": _digest(
            raw.get("content_hash"),
            "recovery_artifact_receipt_hash_invalid",
        ),
        "size_bytes": size_bytes,
        "provenance_summary": {
            "schema": "ananta.recovery_artifact_provenance.v1",
            "authority": "hub",
            "ingress": "workspace",
            "worker_url": _worker_url(
                normalized_provenance.get("worker_url")
            ),
            "manifest_digest": _digest(
                normalized_provenance.get("manifest_digest"),
                "recovery_artifact_receipt_manifest_invalid",
            ),
            "source_index": source_index,
        },
    }


def _bounded_receipt_json(value: object) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as exc:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_not_json"
        ) from exc
    if len(encoded) > MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_receipt_payload_too_large"
        )


def _artifact(
    value: object,
    *,
    expected_index: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_descriptor_invalid"
        )
    raw = dict(value)
    if set(raw) != _ARTIFACT_FIELDS:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_descriptor_fields_invalid"
        )
    source_index = raw.get("source_index")
    if (
        isinstance(source_index, bool)
        or not isinstance(source_index, int)
        or source_index != expected_index
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_source_index_invalid"
        )
    size_bytes = raw.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > MAX_RECOVERY_ARTIFACT_BYTES
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_size_invalid"
        )
    workspace_path = _relative_path(
        raw.get("workspace_path"),
        "recovery_artifact_workspace_path_invalid",
    )
    relative_path = _relative_path(
        raw.get("relative_path"),
        "recovery_artifact_relative_path_invalid",
    )
    if not hmac.compare_digest(
        workspace_path,
        relative_path,
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_path_binding_mismatch"
        )
    return {
        "source_index": source_index,
        "kind": _pattern(
            raw.get("kind"),
            _KIND,
            "recovery_artifact_kind_invalid",
        ),
        "workspace_path": workspace_path,
        "relative_path": relative_path,
        "filename": _filename(raw.get("filename")),
        "media_type": _pattern(
            raw.get("media_type"),
            _MEDIA_TYPE,
            "recovery_artifact_media_type_invalid",
        ),
        "size_bytes": size_bytes,
        "sha256": _digest(
            raw.get("sha256"),
            "recovery_artifact_hash_invalid",
        ),
        "worker_artifact_id": _optional_identifier(
            raw.get("worker_artifact_id")
        ),
        "worker_artifact_version_id": _optional_identifier(
            raw.get("worker_artifact_version_id")
        ),
    }


def _payload_digest(value: Mapping[str, Any]) -> str:
    payload = {
        key: value[key]
        for key in sorted(value)
        if key != "digest"
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_not_json"
        ) from exc
    if len(encoded) > 262_144:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_manifest_too_large"
        )
    return hashlib.sha256(encoded).hexdigest()


def _worker_url(value: object) -> str:
    raw = _bounded_text(
        value,
        maximum_characters=2_048,
        reason_code="recovery_artifact_worker_url_invalid",
    ).strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_worker_url_invalid"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_worker_url_invalid"
        )
    hostname = str(parsed.hostname).lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def _relative_path(value: object, reason_code: str) -> str:
    raw = _bounded_text(
        value,
        maximum_characters=512,
        reason_code=reason_code,
    )
    if (
        not raw
        or raw != raw.strip()
        or "\\" in raw
        or "\x00" in raw
        or len(raw.encode("utf-8")) > 512
    ):
        raise RecoveryArtifactIngressContractError(reason_code)
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or str(path) != raw
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            character in raw
            for character in "\r\n\t"
        )
    ):
        raise RecoveryArtifactIngressContractError(reason_code)
    return str(path)


def _filename(value: object) -> str:
    raw = _bounded_text(
        value,
        maximum_characters=255,
        reason_code="recovery_artifact_filename_invalid",
    )
    if (
        not raw
        or raw != raw.strip()
        or raw in {".", ".."}
        or "/" in raw
        or "\\" in raw
        or "\x00" in raw
        or any(character in raw for character in "\r\n\t")
        or len(raw.encode("utf-8")) > 255
    ):
        raise RecoveryArtifactIngressContractError(
            "recovery_artifact_filename_invalid"
        )
    return raw


def _identifier(value: object, reason_code: str) -> str:
    return _pattern(
        _bounded_text(
            value,
            maximum_characters=256,
            reason_code=reason_code,
        ),
        _IDENTIFIER,
        reason_code,
    )


def _optional_identifier(value: object) -> str | None:
    if value is None:
        return None
    return _identifier(
        value,
        "recovery_artifact_worker_identifier_invalid",
    )


def _pattern(
    value: object,
    pattern: re.Pattern[str],
    reason_code: str,
) -> str:
    raw = _bounded_text(
        value,
        maximum_characters=4_096,
        reason_code=reason_code,
    )
    if not pattern.fullmatch(raw):
        raise RecoveryArtifactIngressContractError(reason_code)
    return raw


def _digest(value: object, reason_code: str) -> str:
    raw = _bounded_text(
        value,
        maximum_characters=64,
        reason_code=reason_code,
    )
    if not _DIGEST.fullmatch(raw):
        raise RecoveryArtifactIngressContractError(reason_code)
    return raw


def _bounded_text(
    value: object,
    *,
    maximum_characters: int,
    reason_code: str,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum_characters
    ):
        raise RecoveryArtifactIngressContractError(reason_code)
    return value


__all__ = [
    "MAX_RECOVERY_ARTIFACT_BYTES",
    "MAX_RECOVERY_ARTIFACT_COUNT",
    "MAX_RECOVERY_ARTIFACT_RECEIPTS_BYTES",
    "MAX_RECOVERY_ARTIFACT_TOTAL_BYTES",
    "MAX_RECOVERY_FORWARD_RESPONSE_BYTES",
    "RECOVERY_ARTIFACT_INGRESS_SCHEMA",
    "RECOVERY_ARTIFACT_RECEIPT_FIELDS",
    "RECOVERY_ARTIFACT_RECEIPTS_SCHEMA",
    "RecoveryArtifactIngressContractError",
    "build_recovery_artifact_ingress_manifest",
    "recovery_artifact_assignment_fingerprint",
    "recovery_artifact_lease_token_digest",
    "validate_recovery_artifact_ingress_manifest",
    "validate_recovery_artifact_receipt_list",
    "validate_recovery_artifact_receipts_payload",
]
