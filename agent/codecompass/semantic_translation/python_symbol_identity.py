"""Deterministic, file-scoped identities for Python semantic symbols."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import quote

PYTHON_SYMBOL_IDENTITY_STRATEGY = "repo_relative_file_symbol_sha256.v1"
_PYTHON_SYMBOL_ID_PREFIX = "semantic:python:symbol:v1"
_MAX_READABLE_SYMBOL_ENCODED_LENGTH = 96
_READABLE_SYMBOL_TRUNCATION_MARKER = "~"
_VALID_SYMBOL_KINDS = frozenset(
    {
        "enum_value",
        "field",
        "function",
        "method",
        "type",
    }
)


class PythonSymbolIdentityPort(Protocol):
    """Narrow identity seam used by the Python semantic adapter."""

    def symbol_id(
        self,
        *,
        path: str,
        symbol_kind: str,
        qualified_symbol: str,
    ) -> str: ...


class DeterministicPythonSymbolIdentityFactory:
    """Build revision-stable IDs from repository path and lexical identity.

    Parser versions, source hashes, line numbers, index revisions and manifest
    identifiers intentionally stay out of the identity. They remain provenance
    metadata on the semantic node, so re-indexing an unchanged declaration does
    not churn its graph identity.

    Existing graph artifacts with the historical unversioned IDs remain
    readable because graph readers treat node IDs as opaque strings. New
    artifacts use the versioned prefix and cannot collide merely because two
    files declare the same symbol name.
    """

    def symbol_id(
        self,
        *,
        path: str,
        symbol_kind: str,
        qualified_symbol: str,
    ) -> str:
        canonical_path = self._canonical_path(path)
        canonical_kind = str(symbol_kind or "").strip().lower()
        canonical_symbol = unicodedata.normalize(
            "NFC",
            str(qualified_symbol or "").strip(),
        )
        if canonical_kind not in _VALID_SYMBOL_KINDS:
            raise ValueError("python_symbol_identity_kind_invalid")
        if not canonical_symbol or "\x00" in canonical_symbol:
            raise ValueError("python_symbol_identity_symbol_invalid")
        identity = json.dumps(
            {
                "path": canonical_path,
                "qualified_symbol": canonical_symbol,
                "strategy": PYTHON_SYMBOL_IDENTITY_STRATEGY,
                "symbol_kind": canonical_kind,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        readable_symbol = self._readable_symbol(canonical_symbol)
        return f"{_PYTHON_SYMBOL_ID_PREFIX}:{canonical_kind}:{readable_symbol}:{digest}"

    @staticmethod
    def _readable_symbol(canonical_symbol: str) -> str:
        encoded_parts: list[str] = []
        encoded_length = 0
        for character in canonical_symbol:
            encoded_character = quote(character, safe="")
            if (
                encoded_length + len(encoded_character)
                > _MAX_READABLE_SYMBOL_ENCODED_LENGTH
            ):
                return (
                    "".join(encoded_parts)
                    + _READABLE_SYMBOL_TRUNCATION_MARKER
                )
            encoded_parts.append(encoded_character)
            encoded_length += len(encoded_character)
        return "".join(encoded_parts)

    @staticmethod
    def _canonical_path(path: str) -> str:
        # A Git path is an identity-bearing provenance value, not display
        # text.  Canonically equivalent Unicode spellings can name two
        # distinct repository entries, so normalizing them here would merge
        # otherwise distinct symbols in the repository graph.
        normalized = str(path or "").replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if not normalized or "\x00" in normalized or parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError("python_symbol_identity_path_invalid")
        canonical = parsed.as_posix().removeprefix("./")
        if not canonical or canonical == ".":
            raise ValueError("python_symbol_identity_path_invalid")
        return canonical


__all__ = [
    "DeterministicPythonSymbolIdentityFactory",
    "PYTHON_SYMBOL_IDENTITY_STRATEGY",
    "PythonSymbolIdentityPort",
]
