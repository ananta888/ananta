"""Optional browser quality transport remains bounded and cannot grant sources."""

from unittest.mock import Mock

import pytest

from ananta_contracts.meet_media_timing import require_media_timing_probe
from tests.test_meet_media_timing_contract import fixture
from worker.meet_media.browser_media_timing import READ_PROBE, SNAPSHOT, START, BrowserMediaTiming


def probe():
    return {
        "schema": "ananta.meet-media-timing-probe.v1",
        "profile": "independent-owned-live-v1",
        "timebase": "browser-performance-v1",
        "max_drift_us": 500_000,
        "max_age_us": 750_000,
        "decoded_video": True,
        "canvas_submission": True,
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "other"},
        {"profile": "lip-sync"},
        {"timebase": "wall"},
        {"max_drift_us": True},
        {"max_drift_us": 500_001},
        {"max_age_us": 750_001},
        {"max_age_us": 750_000.0},
        {"decoded_video": 1},
        {"canvas_submission": 1},
        {"canvas_submission": False},
        {"extra": True},
    ],
)
def test_closed_probe_never_repairs_unsupported_or_looser_profile(patch):
    with pytest.raises(ValueError, match="probe_invalid_or_unsupported"):
        require_media_timing_probe(probe() | patch)


def test_decoder_support_required_only_when_negotiated_and_projection_is_copied():
    value = probe() | {"decoded_video": False}
    assert require_media_timing_probe(value) == value
    assert require_media_timing_probe(value) is not value
    with pytest.raises(ValueError):
        require_media_timing_probe(value, decoded_video=True)
    for key in value:
        with pytest.raises(ValueError):
            require_media_timing_probe({k: v for k, v in value.items() if k != key})


def setup():
    clock = [100.0]
    initial = fixture() | {"sources": {}}
    page = Mock()
    page.evaluate.side_effect = [probe(), initial]
    checkpoint = Mock()
    port = BrowserMediaTiming(page, checkpoint, ["speech.publish"], clock=lambda: clock[0])
    return clock, page, checkpoint, port, initial


def test_exact_optional_start_and_ten_hz_poll_keep_current_checkpoint_on_both_sides():
    clock, page, checkpoint, port, initial = setup()
    assert page.evaluate.call_args_list[0].args == (READ_PROBE,)
    assert page.evaluate.call_args_list[1].args == (START, "independent-owned-live-v1")
    assert checkpoint.call_count == 3
    clock[0] += 0.05
    assert port.poll() is None and page.evaluate.call_count == 2
    clock[0] += 0.06
    current = initial | {"now_us": initial["now_us"] + 110_000}
    page.evaluate.side_effect = [current]
    assert port.poll() == current and checkpoint.call_count == 5
    assert page.evaluate.call_args.args == (SNAPSHOT,)
    port.close()
    with pytest.raises(ValueError, match="closed"):
        port.poll()


@pytest.mark.parametrize("stage", ["probe", "start", "already_active", "checkpoint"])
def test_failed_start_cannot_fall_back_or_open_a_publication(stage):
    page, checkpoint = Mock(), Mock()
    page.evaluate.side_effect = [
        None if stage == "probe" else probe(),
        fixture() if stage == "already_active" else None,
    ]
    if stage == "checkpoint":
        checkpoint.side_effect = ValueError("revoked")
    with pytest.raises(ValueError):
        BrowserMediaTiming(page, checkpoint, ["speech.publish"])
    assert page.evaluate.call_count <= 2
    assert all(call.args[0] in (READ_PROBE, START) for call in page.evaluate.call_args_list)


@pytest.mark.parametrize("fault", ["checkpoint", "rpc", "scope", "frozen_clock", "rollback", "nan"])
def test_quality_or_transport_failure_is_terminal_without_api_reset(fault):
    clock, page, checkpoint, port, initial = setup()
    clock[0] += 0.6
    value = fixture() | {"now_us": 2_600_000}
    if fault == "checkpoint":
        checkpoint.side_effect = ValueError("revoked")
    elif fault == "rpc":
        page.evaluate.side_effect = RuntimeError("renderer")
    elif fault == "scope":
        value["epoch"] += 1
    elif fault == "frozen_clock":
        value = initial
    elif fault == "rollback":
        clock[0] = 99
    elif fault == "nan":
        clock[0] = float("nan")
    if fault != "rpc":
        page.evaluate.side_effect = [value]
    with pytest.raises((ValueError, RuntimeError)):
        port.poll()
    assert port.closed and port.gate.closed
    with pytest.raises(ValueError, match="closed"):
        port.poll()
