"""Deterministic, file-scoped identities for Python semantic symbols."""

from __future__ import annotations

import hashlib
import inspect
import json
import unicodedata
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import quote

PYTHON_SYMBOL_IDENTITY_STRATEGY = (
    "repo_relative_file_provenance_symbol_sha256.v2"
)
_PYTHON_SYMBOL_ID_PREFIX = "semantic:python:symbol:v2"
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
        provenance_line_start: int = 1,
        provenance_column_start: int = 1,
    ) -> str: ...


class LegacyPythonSymbolIdentityPort(Protocol):
    """Pre-v2 injection seam retained for additive adapter compatibility."""

    def symbol_id(
        self,
        *,
        path: str,
        symbol_kind: str,
        qualified_symbol: str,
    ) -> str: ...


class DeterministicPythonSymbolIdentityFactory:
    """Build occurrence IDs from repository path and declaration provenance.

    Parser versions, source hashes, index revisions and manifest identifiers
    intentionally stay out of the identity. The positive declaration start
    line distinguishes otherwise equal lexical symbols within one file.

    Existing graph artifacts with historical unversioned or v1 IDs remain
    readable because graph readers treat node IDs as opaque strings. New v2
    artifacts cannot collide merely because declarations share a file and
    lexical symbol name.
    """

    def symbol_id(
        self,
        *,
        path: str,
        symbol_kind: str,
        qualified_symbol: str,
        provenance_line_start: int = 1,
        provenance_column_start: int = 1,
        _legacy_identity_sha256: str | None = None,
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
        if (
            isinstance(provenance_line_start, bool)
            or not isinstance(provenance_line_start, int)
            or provenance_line_start < 1
            or isinstance(provenance_column_start, bool)
            or not isinstance(provenance_column_start, int)
            or provenance_column_start < 1
        ):
            raise ValueError("python_symbol_identity_provenance_invalid")
        identity = json.dumps(
            {
                "path": canonical_path,
                "provenance_column_start": provenance_column_start,
                "provenance_line_start": provenance_line_start,
                "qualified_symbol": canonical_symbol,
                "strategy": PYTHON_SYMBOL_IDENTITY_STRATEGY,
                "symbol_kind": canonical_kind,
                **(
                    {"legacy_identity_sha256": _legacy_identity_sha256}
                    if _legacy_identity_sha256
                    else {}
                ),
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


def python_occurrence_symbol_id(
    identity: PythonSymbolIdentityPort | LegacyPythonSymbolIdentityPort,
    *,
    path: str,
    symbol_kind: str,
    qualified_symbol: str,
    provenance_line_start: int,
    provenance_column_start: int,
) -> str:
    """Call a v2 identity port or scope a legacy injected ID by provenance."""

    symbol_id = identity.symbol_id
    if _accepts_occurrence_provenance(symbol_id):
        return symbol_id(
            path=path,
            symbol_kind=symbol_kind,
            qualified_symbol=qualified_symbol,
            provenance_line_start=provenance_line_start,
            provenance_column_start=provenance_column_start,
        )
    legacy_id = symbol_id(
        path=path,
        symbol_kind=symbol_kind,
        qualified_symbol=qualified_symbol,
    )
    legacy_digest = hashlib.sha256(str(legacy_id).encode("utf-8")).hexdigest()
    return DeterministicPythonSymbolIdentityFactory().symbol_id(
        path=path,
        symbol_kind=symbol_kind,
        qualified_symbol=qualified_symbol,
        provenance_line_start=provenance_line_start,
        provenance_column_start=provenance_column_start,
        _legacy_identity_sha256=legacy_digest,
    )


def _accepts_occurrence_provenance(symbol_id: object) -> bool:
    try:
        parameters = inspect.signature(symbol_id).parameters.values()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    names = {parameter.name for parameter in parameters}
    return (
        {
            "provenance_line_start",
            "provenance_column_start",
        }
        <= names
        or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    )


__all__ = [
    "DeterministicPythonSymbolIdentityFactory",
    "LegacyPythonSymbolIdentityPort",
    "PYTHON_SYMBOL_IDENTITY_STRATEGY",
    "PythonSymbolIdentityPort",
    "python_occurrence_symbol_id",
]
