"""Process-safe, bounded persistence for local runtime capability snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.local_runtime_capability_contracts import RuntimeModelSnapshot


class LocalRuntimeCapabilityCache:
    def __init__(self, path: str | Path, *, maximum_models: int = 512) -> None:
        self._path = Path(path)
        self._maximum_models = max(1, min(int(maximum_models), 10_000))
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(self._path.suffix + ".lock"))

    def load(self) -> tuple[RuntimeModelSnapshot, ...]:
        with self._transaction:
            if not self._path.is_file() or self._path.is_symlink():
                return ()
            try:
                if self._path.stat().st_size > 16 * 1024 * 1024:
                    return ()
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if raw.get("schema") != "ananta.local-runtime-capability-cache.v1":
                    return ()
                items = raw.get("snapshots")
                if not isinstance(items, list) or len(items) > self._maximum_models:
                    return ()
                snapshots = tuple(RuntimeModelSnapshot.from_mapping(item) for item in items)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return ()
        return snapshots

    def save(self, snapshots: Iterable[RuntimeModelSnapshot]) -> None:
        indexed = {(item.provider_id, item.model_id, item.model_digest): item for item in snapshots}
        ordered = tuple(indexed[key] for key in sorted(indexed))[-self._maximum_models :]
        payload = {
            "schema": "ananta.local-runtime-capability-cache.v1",
            "snapshots": [item.to_dict() for item in ordered],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        with self._transaction:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", dir=self._path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self._path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def replace_provider(self, provider_id: str, snapshots: Iterable[RuntimeModelSnapshot]) -> None:
        # Keep the read/modify/write cycle in one cross-process transaction.
        # ``load`` and ``save`` deliberately reuse this re-entrant lock so two
        # provider refreshes cannot overwrite one another between operations.
        with self._transaction:
            retained = [item for item in self.load() if item.provider_id != provider_id]
            self.save((*retained, *tuple(snapshots)))


__all__ = ["LocalRuntimeCapabilityCache"]
