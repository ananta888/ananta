"""Context Access Policy lifecycle value types and digests (dependency-free)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset({"draft", "active", "superseded", "revoked"})


class ContextPolicyLifecycleError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ContextPolicyActor:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class ContextPolicyVersion:
    policy_id: str
    version: int
    tenant_id: str
    project_id: str
    state: str
    document: Mapping[str, Any]
    policy_digest: str
    etag: str
    created_by: str
    created_at: str

    def __post_init__(self) -> None:
        if self.version < 1 or self.state not in _STATES:
            raise ContextPolicyLifecycleError("policy_version_invalid")
        if not _SHA256.fullmatch(self.policy_digest):
            raise ContextPolicyLifecycleError("policy_digest_invalid")
        if not _SHA256.fullmatch(self.etag):
            raise ContextPolicyLifecycleError("policy_etag_invalid")


@dataclass(frozen=True)
class ContextPolicyDiagnostic:
    severity: str
    reason_code: str
    rule_id: str | None = None


@dataclass(frozen=True)
class ContextPolicyPreview:
    decision: str
    reason_codes: tuple[str, ...]
    matched_rule_path: tuple[str, ...]
    approval_requirement: str | None
    policy_digest: str


def context_policy_payload_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def derive_context_policy_etag(
    *,
    policy_id: str,
    version: int,
    policy_digest: str,
    state: str,
) -> str:
    return context_policy_payload_digest(
        {
            "policy_id": policy_id,
            "version": version,
            "policy_digest": policy_digest,
            "state": state,
        }
    )


def derive_context_policy_digest(
    document: Mapping[str, Any],
) -> str:
    return context_policy_payload_digest(dict(document))


__all__ = [
    "ContextPolicyActor",
    "ContextPolicyDiagnostic",
    "ContextPolicyLifecycleError",
    "ContextPolicyPreview",
    "ContextPolicyVersion",
    "context_policy_payload_digest",
    "derive_context_policy_digest",
    "derive_context_policy_etag",
]
