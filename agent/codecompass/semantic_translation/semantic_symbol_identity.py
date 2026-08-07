"""Deterministic file-scoped identities for semantic adapter nodes.

Canonical language identities such as ``semantic:java:module:java.util.List``
describe what a symbol means, but they do not identify the declaration or
occurrence that supplied its provenance.  This module derives a separate local
node ID while preserving the canonical identity as searchable node metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Final, Protocol
from urllib.parse import quote

SEMANTIC_SYMBOL_IDENTITY_STRATEGY: Final = (
    "repo_relative_file_canonical_symbol_sha256.v1"
)
CANONICAL_SEMANTIC_ID_ATTRIBUTE: Final = "canonical_semantic_id"
SEMANTIC_IDENTITY_STRATEGY_ATTRIBUTE: Final = "semantic_identity_strategy"

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_CANONICAL_ID_LENGTH = 4096
_MAX_QUALIFIER_LENGTH = 4096
_MAX_READABLE_ID_ENCODED_LENGTH = 96


class SemanticSymbolIdentityPort(Protocol):
    """Narrow seam used by language adapters for local graph identities."""

    def symbol_id(
        self,
        *,
        language: str,
        path: str,
        symbol_kind: str,
        canonical_id: str,
        local_qualifier: str,
    ) -> str: ...


class DeterministicSemanticSymbolIdentityFactory:
    """Build a stable local ID from repository provenance and canonical ID."""

    def symbol_id(
        self,
        *,
        language: str,
        path: str,
        symbol_kind: str,
        canonical_id: str,
        local_qualifier: str,
    ) -> str:
        canonical_language = str(language or "").strip().lower()
        canonical_kind = str(symbol_kind or "").strip().lower()
        if not _NAME.fullmatch(canonical_language):
            raise ValueError("semantic_symbol_identity_language_invalid")
        if not _NAME.fullmatch(canonical_kind):
            raise ValueError("semantic_symbol_identity_kind_invalid")
        normalized_path = self._canonical_path(path)
        normalized_canonical_id = self._identity_value(
            canonical_id,
            maximum_length=_MAX_CANONICAL_ID_LENGTH,
            error="semantic_symbol_identity_canonical_id_invalid",
        )
        normalized_qualifier = self._identity_value(
            local_qualifier,
            maximum_length=_MAX_QUALIFIER_LENGTH,
            error="semantic_symbol_identity_qualifier_invalid",
        )
        identity = json.dumps(
            {
                "canonical_id": normalized_canonical_id,
                "language": canonical_language,
                "local_qualifier": normalized_qualifier,
                "path": normalized_path,
                "strategy": SEMANTIC_SYMBOL_IDENTITY_STRATEGY,
                "symbol_kind": canonical_kind,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        readable = self._readable(normalized_qualifier)
        return (
            f"semantic:{canonical_language}:local:v1:"
            f"{canonical_kind}:{readable}:{digest}"
        )

    @staticmethod
    def _identity_value(
        value: str,
        *,
        maximum_length: int,
        error: str,
    ) -> str:
        normalized = str(value or "").strip()
        if (
            not normalized
            or "\x00" in normalized
            or len(normalized) > maximum_length
        ):
            raise ValueError(error)
        return normalized

    @staticmethod
    def _canonical_path(path: str) -> str:
        normalized = str(path or "").replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if (
            not normalized
            or "\x00" in normalized
            or parsed.is_absolute()
            or ".." in parsed.parts
        ):
            raise ValueError("semantic_symbol_identity_path_invalid")
        canonical = parsed.as_posix().removeprefix("./")
        if not canonical or canonical == ".":
            raise ValueError("semantic_symbol_identity_path_invalid")
        return canonical

    @staticmethod
    def _readable(value: str) -> str:
        encoded: list[str] = []
        encoded_length = 0
        for character in value:
            part = quote(character, safe="")
            if encoded_length + len(part) > _MAX_READABLE_ID_ENCODED_LENGTH:
                return "".join(encoded) + "~"
            encoded.append(part)
            encoded_length += len(part)
        return "".join(encoded)


def semantic_identity_attributes(canonical_id: str) -> dict[str, str]:
    """Metadata retained on every node that receives a local identity."""

    normalized = str(canonical_id or "").strip()
    if not normalized or "\x00" in normalized:
        raise ValueError("semantic_symbol_identity_canonical_id_invalid")
    return {
        CANONICAL_SEMANTIC_ID_ATTRIBUTE: normalized,
        SEMANTIC_IDENTITY_STRATEGY_ATTRIBUTE: (
            SEMANTIC_SYMBOL_IDENTITY_STRATEGY
        ),
    }


__all__ = [
    "CANONICAL_SEMANTIC_ID_ATTRIBUTE",
    "DeterministicSemanticSymbolIdentityFactory",
    "SEMANTIC_IDENTITY_STRATEGY_ATTRIBUTE",
    "SEMANTIC_SYMBOL_IDENTITY_STRATEGY",
    "SemanticSymbolIdentityPort",
    "semantic_identity_attributes",
]
