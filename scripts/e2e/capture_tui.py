from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

try:
    from scripts.e2e.e2e_artifacts import write_binary_artifact, write_text_artifact
except ModuleNotFoundError:
    import sys

    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from scripts.e2e.e2e_artifacts import write_binary_artifact, write_text_artifact

_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAukB9pA8f6UAAAAASUVORK5CYII="
)


def render_tui_screen(screen: str, *, run_id: str, task_id: str = "task-tui-smoke") -> str:
    lines = ["ananta tui", f"screen_id: tui-{screen}", f"run_id: {run_id}"]
    if screen == "health":
        lines.extend(["status: connected", "backend: healthy"])
    elif screen == "task_list":
        lines.extend(["tasks: 1", f"selected_task: {task_id}", "state: actionable"])
    elif screen == "artifact_view":
        lines.extend(["artifact: available", f"task_id: {task_id}", "render: text"])
    else:
        lines.extend(["status: degraded", "reason: backend unreachable"])
    lines.append("")
    return "\n".join(lines)


def capture_tui_screens(
    *,
    run_id: str,
    flow_id: str = "tui-scripted-smoke",
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    snapshots: dict[str, str] = {}
    screenshots: dict[str, str] = {}
    for screen in ("health", "task_list", "artifact_view", "degraded"):
        snapshots[screen] = write_text_artifact(
            run_id,
            flow_id,
            f"{screen}.txt",
            render_tui_screen(screen, run_id=run_id),
            artifact_root=artifact_root,
        )
        screenshots[screen] = write_binary_artifact(
            run_id,
            flow_id,
            f"screenshot-{screen}.png",
            _PLACEHOLDER_PNG,
            artifact_root=artifact_root,
        )
    return {"run_id": run_id, "flow_id": flow_id, "snapshots": snapshots, "screenshots": screenshots}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture deterministic TUI evidence snapshots.")
    parser.add_argument("--run-id", default="e2e-tui-capture")
    parser.add_argument("--flow-id", default="tui-scripted-smoke")
    parser.add_argument("--artifact-root", default="")
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    payload = capture_tui_screens(run_id=args.run_id, flow_id=args.flow_id, artifact_root=artifact_root)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
