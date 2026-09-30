"""Bounded, atomic JSON persistence for CodeCompass graph artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class BoundedUtf8Writer:
    """Count serialized UTF-8 bytes before forwarding each JSON write."""

    def __init__(self, handle: Any, *, maximum_bytes: int | None) -> None:
        self._handle = handle
        self._maximum_bytes = maximum_bytes
        self.byte_count = 0

    def write(self, value: str) -> int:
        encoded_size = len(value.encode("utf-8"))
        if (
            self._maximum_bytes is not None
            and self.byte_count + encoded_size > self._maximum_bytes
        ):
            raise RuntimeError("codecompass_graph_artifact_too_large")
        written = self._handle.write(value)
        self.byte_count += encoded_size
        return written


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    max_artifact_bytes: int | None,
) -> None:
    """Write canonical JSON through a same-directory temp file and rename it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            bounded = BoundedUtf8Writer(
                handle,
                maximum_bytes=max_artifact_bytes,
            )
            json.dump(
                payload,
                bounded,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            bounded.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
