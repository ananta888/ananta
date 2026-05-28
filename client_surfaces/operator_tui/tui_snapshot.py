from __future__ import annotations

import os
import re
import time
from pathlib import Path

_ANSI_STRIP = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")


def rendered_tui_snapshot_text(rendered_text: str) -> str:
    lines = _ANSI_STRIP.sub("", str(rendered_text or "")).splitlines()
    body = "\n".join(line.rstrip() for line in lines).rstrip()
    return f"{body}\n" if body else ""


def snapshot_output_dir() -> Path:
    raw = str(os.environ.get("ANANTA_TUI_SNAPSHOT_DIR") or "").strip()
    return Path(raw) if raw else Path(".ananta/tui-snapshots")


def write_tui_snapshot(rendered_text: str, *, now: float | None = None, output_dir: Path | None = None) -> Path:
    root = output_dir or snapshot_output_dir()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(float(now if now is not None else time.time())))
    target = root / f"tui-snapshot-{stamp}.txt"
    index = 2
    while target.exists():
        target = root / f"tui-snapshot-{stamp}-{index}.txt"
        index += 1
    target.write_text(rendered_tui_snapshot_text(rendered_text), encoding="utf-8")
    return target
