from __future__ import annotations

from pathlib import Path

_TEXT_CONTROL_BYTES = frozenset({8, 9, 10, 12, 13, 27})


def read_text_file(path: Path, *, max_bytes: int | None = None) -> str | None:
    """Read a bounded source file once and reject obvious binary payloads."""

    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1 if max_bytes is not None else -1)
        if max_bytes is not None and len(raw) > max_bytes:
            return None
    except OSError:
        return None
    if _looks_binary(raw):
        return None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def _looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\0" in sample:
        return True
    disallowed_controls = sum(
        byte < 32 and byte not in _TEXT_CONTROL_BYTES for byte in sample
    )
    return disallowed_controls / len(sample) > 0.10
