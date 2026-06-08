"""Checkpointing/Resume (SIM-022)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from simulation.models.world_state import WorldState


class CheckpointManager:
    """Saves/loads WorldState snapshots to disk."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: WorldState, label: str | None = None) -> Path:
        label = label or f"tick_{state.tick:06d}"
        path = self.run_dir / f"{label}.json"
        payload = {
            "saved_at": time.time(),
            "state_hash": state.state_hash(),
            "world": state.to_dict(),
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    def load(self, path: str | Path) -> WorldState:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return WorldState.from_dict(data["world"])

    def latest(self) -> Path | None:
        checkpoints = sorted(self.run_dir.glob("tick_*.json"))
        return checkpoints[-1] if checkpoints else None

    def list_checkpoints(self) -> list[dict[str, Any]]:
        result = []
        for p in sorted(self.run_dir.glob("*.json")):
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
                result.append({"file": p.name, "tick": meta.get("world", {}).get("tick"),
                                "hash": meta.get("state_hash"), "saved_at": meta.get("saved_at")})
            except Exception:
                pass
        return result
