"""Immutable Worker egress policy for delegated vector-index embeddings."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

VECTOR_INDEX_EMBEDDING_POLICY_FORBIDDEN = "vector_index_embedding_policy_forbidden"
WORKER_EMBEDDING_EGRESS_ALLOWLIST_ENV = "ANANTA_VECTOR_INDEX_EMBEDDING_EGRESS_ALLOWLIST_JSON"
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/-]*$")


class VectorIndexEmbeddingEgressPolicyError(ValueError):
    """Stable fail-closed error for an unauthorized embedding egress."""

    def __init__(self) -> None:
        super().__init__(VECTOR_INDEX_EMBEDDING_POLICY_FORBIDDEN)


def normalize_embedding_base_url(value: str) -> str:
    """Return one unambiguous HTTPS embedding API base URL."""

    raw = str(value or "").strip()
    if (
        not raw
        or len(raw.encode("utf-8")) > 2048
        or any(ord(character) < 32 for character in raw)
        or "\\" in raw
        or "%" in raw
    ):
        raise VectorIndexEmbeddingEgressPolicyError()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise VectorIndexEmbeddingEgressPolicyError() from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise VectorIndexEmbeddingEgressPolicyError()
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise VectorIndexEmbeddingEgressPolicyError() from exc
    if not host or any(character.isspace() for character in host):
        raise VectorIndexEmbeddingEgressPolicyError()
    path = parsed.path or ""
    if path:
        if not _PATH.fullmatch(path) or "//" in path or any(segment in {".", ".."} for segment in path.split("/")):
            raise VectorIndexEmbeddingEgressPolicyError()
        path = path.rstrip("/")
    display_host = f"[{host}]" if ":" in host else host
    port_suffix = "" if port in {None, 443} else f":{port}"
    return f"https://{display_host}{port_suffix}{path}"


def normalize_embedding_allowlist(
    values: Sequence[str],
) -> tuple[str, ...]:
    """Normalize and de-duplicate an exact embedding base-URL allowlist."""

    if isinstance(values, (str, bytes, bytearray)):
        raise VectorIndexEmbeddingEgressPolicyError()
    normalized = tuple(sorted({normalize_embedding_base_url(value) for value in values}))
    if len(normalized) > 32:
        raise VectorIndexEmbeddingEgressPolicyError()
    return normalized


@dataclass(frozen=True, slots=True)
class WorkerEmbeddingEgressPolicy:
    """Deployment-owned allowlist captured once at Worker composition time."""

    allowed_base_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        raw = tuple(self.allowed_base_urls)
        normalized = normalize_embedding_allowlist(raw)
        if raw != normalized:
            raise VectorIndexEmbeddingEgressPolicyError()
        object.__setattr__(self, "allowed_base_urls", normalized)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "WorkerEmbeddingEgressPolicy":
        source = environ if environ is not None else os.environ
        raw = str(source.get(WORKER_EMBEDDING_EGRESS_ALLOWLIST_ENV) or "").strip()
        if not raw:
            return cls()
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise VectorIndexEmbeddingEgressPolicyError() from exc
        if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
            raise VectorIndexEmbeddingEgressPolicyError()
        normalized = normalize_embedding_allowlist(decoded)
        if list(normalized) != decoded:
            raise VectorIndexEmbeddingEgressPolicyError()
        return cls(allowed_base_urls=normalized)

    def authorize(self, embedding: Mapping[str, Any]) -> None:
        """Reject unapproved external egress without resolving a secret."""

        config = dict(embedding or {})
        provider = str(config.get("provider") or "").strip().lower()
        if provider in {"local", "local_hash", "hash"}:
            return
        if provider not in {"openai", "openai_compatible"}:
            raise VectorIndexEmbeddingEgressPolicyError()
        if config.get("external_calls_allowed") is not True:
            raise VectorIndexEmbeddingEgressPolicyError()
        raw_base_url = str(config.get("base_url") or "").strip()
        base_url = normalize_embedding_base_url(raw_base_url)
        if raw_base_url != base_url or base_url not in self.allowed_base_urls:
            raise VectorIndexEmbeddingEgressPolicyError()
        task_allowed = config.get("allowed_base_urls")
        if not isinstance(task_allowed, list) or any(not isinstance(item, str) for item in task_allowed):
            raise VectorIndexEmbeddingEgressPolicyError()
        normalized_task_allowed = normalize_embedding_allowlist(task_allowed)
        if (
            list(normalized_task_allowed) != task_allowed
            or base_url not in normalized_task_allowed
            or not set(normalized_task_allowed).issubset(self.allowed_base_urls)
        ):
            raise VectorIndexEmbeddingEgressPolicyError()


__all__ = [
    "VECTOR_INDEX_EMBEDDING_POLICY_FORBIDDEN",
    "WORKER_EMBEDDING_EGRESS_ALLOWLIST_ENV",
    "VectorIndexEmbeddingEgressPolicyError",
    "WorkerEmbeddingEgressPolicy",
    "normalize_embedding_allowlist",
    "normalize_embedding_base_url",
]
