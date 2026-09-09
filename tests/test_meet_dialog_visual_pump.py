"""Delegated browser-frame adapter, including late/cancelled native completion."""

import threading
from copy import deepcopy
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_visual_receive import PROFILE, VISUAL_LIMITS
from tests.test_meet_visual_receive import visual_fixture, visual_frames
from worker.meet_media.dialog_visual import DialogVisualPump, start_visual
from worker.meet_media.source_lease import SourceLease
from worker.meet_media.visual_child import analyze

pytestmark = pytest.mark.timeout(45)


def setup():
    job, assignment, sub = visual_fixture()
    assignment["capabilities"] = ["video.receive"]
    frame_values = visual_frames()
    state = {"sequence": 0, "open": True, "closed": 0}
    page, hub = Mock(), Mock()

    def evaluate(expression, arg=None):
        if "probe" in expression:
            return {
                "schema": "ananta.meet-visual-probe.v1",
                "profile": "jpeg-source-v1",
                "supported": True,
                "limits": dict(VISUAL_LIMITS),
            }
        if "visual.open(" in expression:
            return deepcopy(sub)
        if "visual.status()" in expression:
            return state["open"]
        if "visual.close()" in expression:
            state["closed"] += 1
            return None
        if "visual.frame(" in expression:
            state["sequence"] += 1
            return {
                "schema": "ananta.meet-visual-frame.v1",
                "subscriptionId": sub["subscriptionId"],
                "sequence": state["sequence"],
                **frame_values[state["sequence"] - 1],
            }
        raise AssertionError("unexpected browser operation")

    page.evaluate.side_effect = evaluate
    return job, assignment, sub, page, hub, state


def collect(pump):
    for _ in range(3):
        pump.last_frame = -float("inf")
        pump.tick()


def test_three_exact_frames_reach_real_child_and_only_structured_results_cross_hub_port():
    job, assignment, _, page, hub, state = setup()
    pump = DialogVisualPump(page, hub, assignment, job)
    try:
        pump.tick()
        pump.tick()
        assert state["sequence"] == 1  # Local interval cannot be bypassed by frequent ticks.
        for _ in range(2):
            pump.last_frame = -float("inf")
            pump.tick()
        assert pump.stage == "analyze" and pump.frames == []
        pump.pending.result(timeout=5)
        pump.tick()
        pump.pending.result(timeout=2)
        pump.tick()
        assert pump.closed and state["closed"] == 1
        call = hub.call.call_args
        assert call.args == ("visual_result",)
        assert set(call.kwargs) == {"meet_session_id", "visual_task_id", "visual_lease_id", "result"}
        assert call.kwargs["visual_task_id"] == job["task_id"]
        assert call.kwargs["result"]["profile"] == PROFILE
        assert "jpegBase64" not in str(call) and "subscriptionId" not in str(call)
    finally:
        pump.close()


@pytest.mark.parametrize("failure", ["unsupported", "binding", "capability"])
def test_constructor_never_opens_without_negotiation_and_closes_partial_setup(failure):
    job, assignment, _, page, hub, state = setup()
    original = page.evaluate.side_effect
    if failure == "capability":
        assignment["capabilities"] = ["screen.publish"]

    def evaluate(expression, arg=None):
        value = original(expression, arg)
        if failure == "unsupported" and "probe" in expression:
            value["supported"] = False
        if failure == "binding" and "visual.open(" in expression:
            value["binding"]["publication_epoch"] += 1
        return value

    page.evaluate.side_effect = evaluate
    with pytest.raises(ValueError):
        DialogVisualPump(page, hub, assignment, job)
    assert not any("visual.frame(" in call.args[0] for call in page.evaluate.call_args_list)
    if failure != "binding":
        assert not any("visual.open(" in call.args[0] for call in page.evaluate.call_args_list)
    assert state["closed"] == int(failure != "capability")
    hub.call.assert_not_called()


@pytest.mark.parametrize("failure", ["browser", "job", "frame"])
def test_revoked_browser_job_or_malformed_frame_never_starts_analysis(failure):
    job, assignment, _, page, hub, state = setup()
    pump = DialogVisualPump(page, hub, assignment, job)
    try:
        if failure == "browser":
            state["open"] = False
        if failure == "job":
            pump.lease.close()
        if failure == "frame":
            original = page.evaluate.side_effect

            def evaluate(expression, arg=None):
                value = original(expression, arg)
                if "visual.frame(" in expression:
                    value["subscriptionId"] = "wrong"
                return value

            page.evaluate.side_effect = evaluate
        with pytest.raises(ValueError):
            pump.tick()
        assert pump.pending is None and pump.frames == []
        hub.call.assert_not_called()
    finally:
        pump.close()


def test_close_drops_late_native_result_and_does_not_submit_hub_completion(monkeypatch):
    job, assignment, _, page, hub, _ = setup()
    entered, release = threading.Event(), threading.Event()
    pipeline = Mock()

    def run(frames):
        entered.set()
        assert release.wait(2), "bounded synthetic analysis release"
        return analyze({"profile": PROFILE, "frames": frames})

    pipeline.analyze.side_effect = run
    monkeypatch.setattr("worker.meet_media.dialog_visual.MeetVisualPipeline", Mock(return_value=pipeline))
    pump = DialogVisualPump(page, hub, assignment, job)
    try:
        collect(pump)
        assert entered.wait(1)
        future = pump.pending
        pump.close()
        release.set()
        future.result(timeout=2)
        pump.tick()
        assert pump.pending is None and pump.frames == []
        pipeline.cancel.assert_called_once()
        hub.call.assert_not_called()
    finally:
        release.set()
        pump.close()


def test_mutating_an_original_binding_or_job_does_not_mutate_the_lease_snapshot():
    job, _, sub = visual_fixture()
    binding = sub["binding"]
    lease = SourceLease(binding, job)
    lease.require(binding)
    binding["publication_epoch"] += 1
    job["deadline"] += 1000
    with pytest.raises(ValueError):
        lease.require(binding)
    assert lease.job["deadline"] != job["deadline"]


@pytest.mark.parametrize("kind", ["off", "missing", "claimed", "source-mismatch"])
def test_runtime_never_requests_unselected_or_unavailable_visual_work(kind):
    job, assignment, _, page, hub, _ = setup()
    state = {
        "controls": {"visual": {"enabled": True}},
        "visual_job": None,
        "authorization": {
            "publications": [
                {
                    "peerId": job["peer_id"],
                    "publicationId": job["publication_id"],
                    "source": job["source"],
                    "publicationEpoch": job["publication_epoch"],
                }
            ]
        },
    }
    if kind == "off":
        state["controls"]["visual"]["enabled"] = False
    if kind == "missing":
        assignment["capabilities"] = ["audio.receive"]
    if kind == "claimed":
        state["visual_job"] = job
    if kind == "source-mismatch":
        page.evaluate.side_effect = None
        page.evaluate.return_value = [{"publicationId": "other"}]
    assert start_visual(page, hub, assignment, state, job["meet_session_id"]) is None
    hub.call.assert_not_called()
