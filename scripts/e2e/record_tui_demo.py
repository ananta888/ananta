from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.e2e.e2e_artifacts import write_text_artifact
except ModuleNotFoundError:
    import sys

    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from scripts.e2e.e2e_artifacts import write_text_artifact


def record_tui_demo(
    *,
    run_id: str,
    flow_id: str = "tui-demo-video",
    enabled: bool = False,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "skipped",
            "optional": True,
            "reason": "video capture disabled (set --enable to record)",
            "video_ref": "",
        }
    video_ref = write_text_artifact(
        run_id,
        flow_id,
        "video-tui-demo.cast",
        "\n".join(
            [
                "schema: asciinema-placeholder-v1",
                f"run_id: {run_id}",
                "flow: tui_demo",
                "events: []",
                "",
            ]
        ),
        artifact_root=artifact_root,
    )
    return {"status": "recorded", "optional": True, "reason": "", "video_ref": video_ref}


def main() -> int:
    parser = argparse.ArgumentParser(description="Record optional TUI demo evidence.")
    parser.add_argument("--run-id", default="e2e-tui-demo")
    parser.add_argument("--flow-id", default="tui-demo-video")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--artifact-root", default="")
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    payload = record_tui_demo(
        run_id=args.run_id,
        flow_id=args.flow_id,
        enabled=args.enable,
        artifact_root=artifact_root,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
