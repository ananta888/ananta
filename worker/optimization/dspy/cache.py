"""Per-job memory cache with fully scoped deterministic keys."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from ananta_contracts.dspy_optimization import canonical_digest, require_digest, require_id


class DspyRunMemoryCache:
    _KEY_FIELDS = {
        "tenant_id",
        "dataset_digest",
        "program_digest",
        "model_digest",
        "provider_digest",
        "optimizer_digest",
        "metric_digest",
        "config_digest",
        "request_digest",
    }

    def __init__(self, *, max_entries: int = 1_000) -> None:
        if not 1 <= max_entries <= 100_000:
            raise ValueError("dspy_cache_capacity_invalid")
        self._max_entries = max_entries
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()

    def key(self, binding: Mapping[str, str]) -> str:
        if set(binding) != self._KEY_FIELDS:
            raise ValueError("dspy_cache_binding_invalid")
        require_id(binding["tenant_id"], "tenant_id")
        for field in self._KEY_FIELDS - {"tenant_id"}:
            require_digest(binding[field], field)
        return canonical_digest(dict(sorted(binding.items())))

    def put(self, binding: Mapping[str, str], value: Any) -> str:
        key = self.key(binding)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_entries:
                raise RuntimeError("dspy_cache_capacity_exhausted")
            self._values[key] = value
        return key

    def get(self, binding: Mapping[str, str]) -> Any | None:
        with self._lock:
            return self._values.get(self.key(binding))

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


__all__ = ["DspyRunMemoryCache"]
