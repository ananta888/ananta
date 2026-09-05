"""Observe durable Trainer checkpoints even when an engine bypasses callbacks."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

_CHECKPOINT_RE = re.compile(r"^checkpoint-([1-9][0-9]*)$")


class TrainerCheckpointObserver:
    """Publish stable direct-child checkpoints through the worker event port."""

    def __init__(
        self,
        *,
        root: Path,
        emit: Callable[[str, Mapping[str, Any]], None],
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._root = root
        self._emit = emit
        self._poll_interval_seconds = max(0.01, poll_interval_seconds)
        self._candidates: dict[str, tuple[tuple[str, int, int], ...]] = {}
        self._published: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("trainer_checkpoint_observer_already_started")
        self._thread = threading.Thread(
            target=self._run,
            name="trainer-checkpoint-observer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._poll_interval_seconds * 4))

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def scan_once(self) -> None:
        if not self._root.is_dir():
            return
        for checkpoint in sorted(self._root.iterdir(), key=lambda path: path.name):
            match = _CHECKPOINT_RE.fullmatch(checkpoint.name)
            if match is None or checkpoint.name in self._published:
                continue
            signature = self._stable_candidate_signature(checkpoint)
            if signature is None:
                continue
            if self._candidates.get(checkpoint.name) != signature:
                self._candidates[checkpoint.name] = signature
                continue
            self._emit(
                "checkpoint",
                {"step": int(match.group(1)), "name": checkpoint.name},
            )
            self._published.add(checkpoint.name)
            self._candidates.pop(checkpoint.name, None)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval_seconds):
            try:
                self.scan_once()
            except BaseException as exc:  # propagated in the training thread
                self._error = exc
                self._stop.set()

    @staticmethod
    def _stable_candidate_signature(
        checkpoint: Path,
    ) -> tuple[tuple[str, int, int], ...] | None:
        if not checkpoint.is_dir() or checkpoint.is_symlink():
            return None
        trainer_state = checkpoint / "trainer_state.json"
        if not trainer_state.is_file() or trainer_state.is_symlink():
            return None
        entries = list(checkpoint.rglob("*"))
        if any(path.is_symlink() for path in entries):
            return None
        files = sorted((path for path in entries if path.is_file()), key=lambda path: path.as_posix())
        if not files or any(not path.is_file() for path in entries if not path.is_dir()):
            return None
        try:
            return tuple(
                (
                    path.relative_to(checkpoint).as_posix(),
                    int(path.stat().st_size),
                    int(path.stat().st_mtime_ns),
                )
                for path in files
            )
        except OSError:
            return None
