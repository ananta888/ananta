"""Run Artifact Layout (SIM-024).

Manages the on-disk directory structure for a simulation run.

  runs/<run_id>/
    checkpoints/          ← tick_XXXXXX.json
    events/               ← events_XXXXXX.jsonl
    report.json
    replay_trace.json
    config.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from simulation.models.world_state import SimEvent


class RunArtifactLayout:

    def __init__(self, base_dir: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.root = Path(base_dir) / run_id
        self.checkpoints_dir = self.root / "checkpoints"
        self.events_dir = self.root / "events"
        for d in (self.root, self.checkpoints_dir, self.events_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save_config(self, config: dict[str, Any]) -> None:
        (self.root / "config.json").write_text(
            json.dumps(config, indent=2, default=str), encoding="utf-8"
        )

    def checkpoint_path(self, tick: int) -> Path:
        return self.checkpoints_dir / f"tick_{tick:06d}.json"

    def events_path(self, tick: int) -> Path:
        return self.events_dir / f"events_{tick:06d}.jsonl"

    def append_events(self, tick: int, events: list[SimEvent]) -> None:
        path = self.events_path(tick)
        with path.open("a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev.as_dict(), default=str) + "\n")

    def report_path(self) -> Path:
        return self.root / "report.json"

    def replay_trace_path(self) -> Path:
        return self.root / "replay_trace.json"

    def list_checkpoints(self) -> list[Path]:
        return sorted(self.checkpoints_dir.glob("tick_*.json"))

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "checkpoints": len(self.list_checkpoints()),
            "has_report": self.report_path().exists(),
        }
