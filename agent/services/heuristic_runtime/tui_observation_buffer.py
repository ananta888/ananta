"""TuiObservationBuffer — Ringbuffer für letzte N TUI-Snapshots und Deltas."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class SnapshotRef:
    frame_id: str
    screen_hash: str
    timestamp: float
    width: int
    height: int
    cells_summary: dict[str, Any] = field(default_factory=dict)  # kompakte Zusammenfassung

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "screen_hash": self.screen_hash,
            "timestamp": self.timestamp,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class DeltaRef:
    previous_hash: str
    current_hash: str
    changed_cell_count: int
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "changed_cell_count": self.changed_cell_count,
            "timestamp": self.timestamp,
        }


class TuiObservationBuffer:
    """Ringbuffer: hält letzte max_snapshots Snapshots und max_deltas Deltas."""

    def __init__(self, max_snapshots: int = 20, max_deltas: int = 100) -> None:
        self._max_snapshots = max_snapshots
        self._max_deltas = max_deltas
        self._snapshots: deque[SnapshotRef] = deque(maxlen=max_snapshots)
        self._deltas: deque[DeltaRef] = deque(maxlen=max_deltas)
        self._by_hash: dict[str, SnapshotRef] = {}
        self._by_frame: dict[str, SnapshotRef] = {}

    def push_snapshot(self, ref: SnapshotRef) -> None:
        if len(self._snapshots) == self._max_snapshots:
            evicted = self._snapshots[0]
            self._by_hash.pop(evicted.screen_hash, None)
            self._by_frame.pop(evicted.frame_id, None)
        self._snapshots.append(ref)
        self._by_hash[ref.screen_hash] = ref
        self._by_frame[ref.frame_id] = ref

    def push_delta(self, delta: DeltaRef) -> None:
        self._deltas.append(delta)

    def get_by_hash(self, screen_hash: str) -> SnapshotRef | None:
        return self._by_hash.get(screen_hash)

    def get_by_frame(self, frame_id: str) -> SnapshotRef | None:
        return self._by_frame.get(frame_id)

    def latest_snapshots(self, n: int = 5) -> list[SnapshotRef]:
        refs = list(self._snapshots)
        return refs[-n:]

    def snapshots_in_window(self, start_ts: float, end_ts: float) -> list[SnapshotRef]:
        return [s for s in self._snapshots if start_ts <= s.timestamp <= end_ts]

    def llm_observation_pack(self, n_snapshots: int = 3) -> dict[str, Any]:
        """Kompaktes Pack für Hintergrundanalyse — keine vollständigen Zellmatrizen."""
        recent = self.latest_snapshots(n_snapshots)
        recent_deltas = list(self._deltas)[-10:]
        return {
            "snapshot_count": len(self._snapshots),
            "delta_count": len(self._deltas),
            "recent_snapshots": [s.to_dict() for s in recent],
            "recent_deltas": [d.to_dict() for d in recent_deltas],
        }

    def __len__(self) -> int:
        return len(self._snapshots)
