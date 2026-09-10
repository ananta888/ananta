"""Immutable scheduler-declared Pi runtime facts; never an evidence issuer.

The Hub must admit the origin and bind these facts to a registered Worker.
Parsing this wire contract alone does not attest a deployment or a model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from urllib.parse import urlsplit

from ananta_contracts.native_context_bundle import native_context_digest


@dataclass(frozen=True, slots=True)
class PiRuntimeManifest:
    tenant_id: str
    project_id: str
    worker_id: str
    worker_url: str
    repository_revision: str
    image_digest: str
    node_version: str
    pi_version: str
    evidence_scope: str
    synthetic: bool
    execution_profile: str = "pi-sdk-no-tools-v1"
    schema: str = "ananta.pi-runtime-manifest.v1"

    def __post_init__(self):
        if (
            any(not _identifier(getattr(self, key)) for key in ("tenant_id", "project_id", "worker_id"))
            or not _worker_url(self.worker_url)
            or not isinstance(self.repository_revision, str)
            or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", self.repository_revision) is None
            or not isinstance(self.image_digest, str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest) is None
            or not _node_version(self.node_version) or self.pi_version != "0.85.1"
            or self.execution_profile != "pi-sdk-no-tools-v1" or self.schema != "ananta.pi-runtime-manifest.v1"
            or not isinstance(self.evidence_scope, str)
            or self.evidence_scope not in {"test", "local", "external", "production"}
            or type(self.synthetic) is not bool or (self.synthetic and self.evidence_scope != "test")
        ):
            raise ValueError("pi_runtime_manifest_invalid")

    @classmethod
    def from_mapping(cls, raw) -> PiRuntimeManifest:
        if not isinstance(raw, Mapping) or set(raw) != {field.name for field in fields(cls)}:
            raise ValueError("pi_runtime_manifest_fields_invalid")
        return cls(**dict(raw))

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def digest(self) -> str:
        return native_context_digest(self.to_dict())

    @property
    def environment_digest(self) -> str:
        return native_context_digest({
            "schema": "ananta.pi-runtime-environment.v1",
            "repository_revision": self.repository_revision, "image_digest": self.image_digest,
            "node_version": self.node_version, "pi_version": self.pi_version,
            "execution_profile": self.execution_profile,
        })


def _identifier(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 191 and not any(ord(char) < 33 for char in value)


def _node_version(value) -> bool:
    if not isinstance(value, str) or len(value) > 32 or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is None:
        return False
    return tuple(map(int, value.split("."))) >= (22, 19, 0)


def _worker_url(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(char) < 33 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and value == value.rstrip("/")
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        return False
