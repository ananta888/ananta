"""Identity-based leases for safely replacing retrieval runtimes."""

from __future__ import annotations

import threading
from collections.abc import Iterable


class RetrievalRuntimeLeaseRegistry:
    """Defer lifecycle cleanup until all in-flight users have released."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[int, int] = {}
        self._retired: dict[int, object] = {}

    def acquire(self, runtime: object) -> None:
        identity = id(runtime)
        with self._lock:
            if identity in self._retired:
                raise RuntimeError("retrieval_runtime_already_retired")
            self._active[identity] = self._active.get(identity, 0) + 1

    def release(self, runtime: object) -> object | None:
        identity = id(runtime)
        with self._lock:
            count = self._active.get(identity, 0)
            if count <= 0:
                raise RuntimeError("retrieval_runtime_lease_not_held")
            if count > 1:
                self._active[identity] = count - 1
                return None
            self._active.pop(identity, None)
            return self._retired.pop(identity, None)

    def retire(self, runtime: object | None) -> object | None:
        if runtime is None:
            return None
        identity = id(runtime)
        with self._lock:
            if self._active.get(identity, 0) > 0:
                self._retired[identity] = runtime
                return None
            return runtime

    def retire_all(
        self,
        runtimes: Iterable[object],
    ) -> tuple[object, ...]:
        ready: dict[int, object] = {}
        for runtime in runtimes:
            candidate = self.retire(runtime)
            if candidate is not None:
                ready[id(candidate)] = candidate
        return tuple(ready.values())


__all__ = ["RetrievalRuntimeLeaseRegistry"]
