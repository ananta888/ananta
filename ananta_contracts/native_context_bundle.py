"""Closed, content-bound projection of Hub-approved Native task context.

Hashes bind transport content; they are not evidence identities or grants.
Only an authenticated Hub decision may supply this projection.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

NATIVE_CONTEXT_SCHEMA = "ananta.native-context-bundle.v1"
MAX_NATIVE_CONTEXT_BYTES = 24_000
_DIGEST = re.compile(r"[0-9a-f]{64}")


def native_context_digest(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeApprovedContext:
    """Output of the Hub policy port, not a Worker policy decision."""

    content: str
    policy_digest: str

    def assert_valid(self) -> None:
        if not isinstance(self.content, str) or len(self.content.encode("utf-8")) > MAX_NATIVE_CONTEXT_BYTES:
            raise ValueError("native_context_content_invalid")
        if not isinstance(self.policy_digest, str) or _DIGEST.fullmatch(self.policy_digest) is None:
            raise ValueError("native_context_policy_digest_invalid")


@dataclass(frozen=True, slots=True)
class NativeContextBundleProjection:
    hub_task_id: str
    bundle_id: str
    command_digest: str
    policy_digest: str
    content: str
    content_digest: str
    schema: str = NATIVE_CONTEXT_SCHEMA

    @classmethod
    def create(
        cls, *, hub_task_id: str, bundle_id: str, command: Mapping[str, Any], approved: NativeApprovedContext,
    ) -> NativeContextBundleProjection:
        approved.assert_valid()
        value = cls(
            hub_task_id, bundle_id, native_context_digest(dict(command)), approved.policy_digest,
            approved.content, native_context_digest(approved.content),
        )
        value.assert_valid()
        return value

    @classmethod
    def from_mapping(cls, raw: object) -> NativeContextBundleProjection:
        keys = {"schema", "hub_task_id", "bundle_id", "command_digest", "policy_digest", "content", "content_digest"}
        if not isinstance(raw, Mapping) or set(raw) != keys or any(not isinstance(v, str) for v in raw.values()):
            raise ValueError("native_context_projection_invalid")
        value = cls(**dict(raw))
        value.assert_valid()
        return value

    def assert_valid(self) -> None:
        NativeApprovedContext(self.content, self.policy_digest).assert_valid()
        if self.schema != NATIVE_CONTEXT_SCHEMA:
            raise ValueError("native_context_schema_invalid")
        for value in (self.hub_task_id, self.bundle_id):
            if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 for c in value):
                raise ValueError("native_context_identifier_invalid")
        if not isinstance(self.command_digest, str) or _DIGEST.fullmatch(self.command_digest) is None:
            raise ValueError("native_context_command_digest_invalid")
        if self.content_digest != native_context_digest(self.content):
            raise ValueError("native_context_content_digest_mismatch")

    def to_dict(self) -> dict[str, str]:
        self.assert_valid()
        return asdict(self)
