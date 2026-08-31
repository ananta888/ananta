"""Provider-neutral request limits for local model runtimes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class LocalRuntimeRequestPolicy:
    def __init__(self, *, maximum_payload_bytes: int = 8 * 1024 * 1024) -> None:
        self._maximum_payload_bytes = max(1024, min(int(maximum_payload_bytes), 64 * 1024 * 1024))

    @staticmethod
    def effective_context_window(*limits: int | None) -> int | None:
        verified = [int(value) for value in limits if type(value) is int and value > 0]
        return min(verified) if verified else None

    def validate_payload(self, payload: Mapping[str, Any]) -> int:
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("local_runtime_payload_invalid") from exc
        if len(encoded) > self._maximum_payload_bytes:
            raise ValueError("local_runtime_payload_too_large")
        return len(encoded)


__all__ = ["LocalRuntimeRequestPolicy"]
