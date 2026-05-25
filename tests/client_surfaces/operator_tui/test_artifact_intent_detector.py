from __future__ import annotations

from client_surfaces.operator_tui.artifact_intent import ArtifactIntentDetector, IntentConfidence
from client_surfaces.operator_tui.mouse import MouseState
from client_surfaces.operator_tui.region_index import RegionTarget


def test_hover_dwell_creates_likely_intent() -> None:
    detector = ArtifactIntentDetector(dwell_seconds=0.3)
    target = RegionTarget(
        kind="artifact",
        section_id="artifacts",
        pane="content",
        label="Cast file",
        payload={"selected_index": 0, "path": "tests/output/operator_tui_splash.cast"},
    )
    mouse = MouseState(x=20, y=10, active=True, hover_started_at=1.0, last_seen_at=1.6, last_event_type="move")
    intent = detector.evaluate(
        now=1.6,
        mouse=mouse,
        target=target,
        selected_index=0,
        current_section_id="artifacts",
        user_feed="",
    )
    assert intent.confidence in {IntentConfidence.LIKELY, IntentConfidence.CONFIRMED}


def test_click_confirms_artifact_intent() -> None:
    detector = ArtifactIntentDetector(dwell_seconds=1.0)
    target = RegionTarget(kind="artifact", section_id="artifacts", pane="content", label="Cast", payload={"selected_index": 1})
    mouse = MouseState(x=2, y=2, active=True, hover_started_at=4.0, last_seen_at=4.1, last_event_type="down", buttons=1)
    intent = detector.evaluate(
        now=4.1,
        mouse=mouse,
        target=target,
        selected_index=1,
        current_section_id="artifacts",
        user_feed="",
    )
    assert intent.confidence == IntentConfidence.CONFIRMED


def test_fast_mouse_pass_does_not_confirm() -> None:
    detector = ArtifactIntentDetector(dwell_seconds=0.5)
    target = RegionTarget(kind="artifact", section_id="artifacts", pane="content", label="tmp", payload={})
    mouse = MouseState(x=1, y=1, active=True, hover_started_at=9.95, last_seen_at=10.0, last_event_type="move")
    intent = detector.evaluate(
        now=10.0,
        mouse=mouse,
        target=target,
        selected_index=0,
        current_section_id="artifacts",
        user_feed="",
    )
    assert intent.confidence in {IntentConfidence.NONE, IntentConfidence.WEAK}
