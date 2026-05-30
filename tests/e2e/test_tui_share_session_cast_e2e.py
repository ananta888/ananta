from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    path = Path(ref)
    return path if path.is_absolute() else ROOT / path


def test_share_session_cast_contains_create_and_list_flow(tmp_path: Path) -> None:
    payload = record_tui_demo(
        run_id="video-share-session-e2e",
        flow_id="tui-share-session-e2e-video",
        enabled=True,
        scene="share-session-e2e",
        artifact_root=tmp_path / "artifacts",
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(str(payload["video_ref"]))
    assert video_path.exists()
    assert video_path.name == "video-tui-share-session-e2e.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert "Share Session E2E" in header["title"]

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", frame_text)

    assert ":share create test" in plain
    assert "[share-create] status=ok" in plain
    assert "session_id=share-test-001" in plain
    assert ":share list" in plain
    assert "[share-list] count=1" in plain
    assert "share-test-001  test   admin  active  1" in plain
