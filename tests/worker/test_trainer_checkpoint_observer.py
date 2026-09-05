from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from worker.training.trainer_checkpoint_observer import TrainerCheckpointObserver


def test_observer_publishes_only_stable_complete_direct_checkpoint(tmp_path: Path) -> None:
    events: list[tuple[str, Mapping[str, Any]]] = []
    observer = TrainerCheckpointObserver(
        root=tmp_path,
        emit=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    checkpoint = tmp_path / "checkpoint-7"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"model")

    observer.scan_once()
    assert events == []

    (checkpoint / "trainer_state.json").write_text("{}", encoding="utf-8")
    observer.scan_once()
    assert events == []
    observer.scan_once()
    observer.scan_once()

    assert events == [("checkpoint", {"step": 7, "name": "checkpoint-7"})]


def test_observer_restarts_stability_window_when_checkpoint_changes(tmp_path: Path) -> None:
    events: list[tuple[str, Mapping[str, Any]]] = []
    observer = TrainerCheckpointObserver(
        root=tmp_path,
        emit=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    checkpoint = tmp_path / "checkpoint-3"
    checkpoint.mkdir()
    state = checkpoint / "trainer_state.json"
    state.write_text("{}", encoding="utf-8")

    observer.scan_once()
    state.write_text('{"step":3}', encoding="utf-8")
    observer.scan_once()
    assert events == []
    observer.scan_once()

    assert events == [("checkpoint", {"step": 3, "name": "checkpoint-3"})]
