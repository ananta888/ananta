"""Shared primitives for the notation renderer subsystem.

All other notation_*.py modules import from here.  Nothing in this
module imports from sibling notation modules — it is the leaf of the
dependency tree.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PSEUDOSTATE = "[*]"


class NotationRenderError(ValueError):
    """Raised when a notation pattern cannot be rendered safely.

    The error message is safe to log — it does not include the
    rendered source.
    """


@dataclass(frozen=True)
class NotationArtifact:
    """One rendered notation output.

    For notation patterns a single render produces one source file
    (Mermaid ``.mmd`` or BPMN ``.bpmn``).
    """

    pattern_id: str
    language: str
    source: str
    sha256: str
    bytes_written: int
    output_filename: str

    @property
    def manifest_sha256(self) -> str:
        """Stable hash of the full render metadata."""
        payload = (
            f"{self.pattern_id}\t{self.language}\t"
            f"{self.output_filename}\t{self.sha256}\t{self.bytes_written}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "source": self.source,
            "sha256": self.sha256,
            "bytes_written": self.bytes_written,
            "output_filename": self.output_filename,
            "manifest_sha256": self.manifest_sha256,
        }


def _as_str(value: Any, *, field: str) -> str:
    if value is None:
        raise NotationRenderError(f"parameter {field!r} is required")
    if not isinstance(value, str):
        raise NotationRenderError(
            f"parameter {field!r} must be a string, got {type(value).__name__}"
        )
    return value


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return bool(value)


def _as_list(value: Any, *, field: str) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in stripped.split(",") if item.strip()]
    raise NotationRenderError(
        f"parameter {field!r} must be a list or string, got {type(value).__name__}"
    )


def _as_dict_entries(values: Iterable, *, field: str) -> list[dict]:
    """Coerce a list of (json-encoded string | dict) into a list of dicts."""
    result: list[dict] = []
    for idx, item in enumerate(values):
        if isinstance(item, dict):
            result.append(item)
            continue
        if isinstance(item, str):
            stripped = item.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise NotationRenderError(
                    f"{field}[{idx}] is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise NotationRenderError(
                    f"{field}[{idx}] must decode to a JSON object, got "
                    f"{type(parsed).__name__}"
                )
            result.append(parsed)
            continue
        raise NotationRenderError(
            f"{field}[{idx}] must be a dict or JSON string, got "
            f"{type(item).__name__}"
        )
    return result


def _check_identifier(value: str, *, field: str) -> str:
    if not _IDENT_RE.match(value):
        raise NotationRenderError(
            f"{field!r} {value!r} must match ^[A-Za-z_][A-Za-z0-9_]*$"
        )
    return value
