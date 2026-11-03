"""Private, atomic Hub journal and cross-process project lock."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path


class Journal:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "journal.json"
        self._depth = threading.local()

    @contextmanager
    def locked(self, *, blocking=True):
        # flock() on a fresh descriptor is not reentrant: a nested locked()
        # from the same thread (retry -> status update -> dispatch callback)
        # would block forever on its own outer lock. Cross-process exclusion
        # keeps the flock; same-thread nesting only deepens a counter.
        depth = getattr(self._depth, "value", 0)
        if depth:
            self._depth.value = depth + 1
            try:
                yield
            finally:
                self._depth.value = depth
            return
        with (self.root / "project.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self._depth.value = 1
            try:
                yield
            finally:
                self._depth.value = 0
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {"items": {}}

    def write(self, state):
        temporary = self.root / "journal.tmp"
        with temporary.open("w") as stream:
            os.chmod(temporary, 0o600)
            json.dump(state, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)


def _same(left, right) -> bool:
    """JSON-normalized equality.

    Asdict results carry tuples where the journal roundtrip yields lists;
    comparing serialized forms keeps content equality honest.
    """
    return json.dumps(left, sort_keys=True, ensure_ascii=True) == json.dumps(right, sort_keys=True, ensure_ascii=True)


class JournalTransaction:
    """Optimistic read-modify-write on the journal with bounded lock holds.

    The dispatch lifecycle reads the journal once, runs its long operations
    (worker execution, planner LLM calls, publication pushes) WITHOUT holding
    the project lock, and flushes tracked item mutations later. A flush merges
    only items that are unmodified since our last write (per-item optimistic
    guard): concurrent admin changes win, and an owned item that changed
    underneath the attempt marks it superseded so its results are discarded.
    """

    def __init__(self, journal: Journal):
        self._journal = journal
        self.state: dict = {"items": {}, "session": {}}
        self._baseline: dict = {"items": {}, "session": {}}

    def begin(self) -> dict:
        """Snapshot the journal under the lock; the working copy stays unlocked."""
        with self._journal.locked():
            self.state = self._journal.read()
            self._baseline = copy.deepcopy(self.state)
        return self.state

    def _touched_keys(self) -> list[str]:
        ours = self.state.get("items") or {}
        base = self._baseline.get("items") or {}
        return sorted(set(ours) | set(base), key=str)

    def write(self, *, owned_key: str | None = None) -> bool:
        """Merge tracked mutations onto the live journal under the lock.

        Returns False when ``owned_key`` was modified by someone else since
        our last flush (the attempt is superseded and its item changes are
        discarded). Untouched items always keep their live versions.
        """
        state_items = self.state.get("items") or {}
        base_items = self._baseline.get("items") or {}
        touched = [key for key in self._touched_keys() if not _same(state_items.get(key), base_items.get(key))]
        dispatched_delta = int((self.state.get("session") or {}).get("dispatched", 0)) - int(
            (self._baseline.get("session") or {}).get("dispatched", 0)
        )
        superseded = False
        with self._journal.locked():
            fresh = self._journal.read()
            fresh_items = fresh.setdefault("items", {})
            for key in touched:
                ours = state_items.get(key)
                base = base_items.get(key)
                live = fresh_items.get(key)
                if owned_key is not None and key == owned_key:
                    if not _same(live, base):
                        superseded = True
                        continue
                elif not _same(live, base):
                    continue
                if ours is None:
                    fresh_items.pop(key, None)
                else:
                    fresh_items[key] = ours
            if dispatched_delta:
                session = fresh.setdefault("session", {})
                session["dispatched"] = int(session.get("dispatched", 0)) + dispatched_delta
            self._journal.write(fresh)
        self._baseline = copy.deepcopy(self.state)
        return not superseded

    def discard(self) -> None:
        """Abandon the transaction; nothing is merged."""
        self.state = {"items": {}, "session": {}}
        self._baseline = {"items": {}, "session": {}}
