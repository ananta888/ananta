"""Deterministic bounded endpoint detection and immutable Hub segment authority."""

import struct
from dataclasses import replace

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_audio_profile import AudioReceiveProfile, parse_audio_profile
from ananta_contracts.meet_audio_segment import require_segment_finished, require_segment_probe
from tests.test_meet_dialog_audio import runtime
from worker.meet_media.audio_segment import EnergySegment, FixedSegment, segment_boundary

SPEECH = struct.pack("<h", 2000) * 1600
QUIET = b"\0" * 3200
PROFILE = AudioReceiveProfile(segment_seconds=3, segmentation="energy-v1")


def test_optional_strategy_is_closed_and_legacy_projection_is_unchanged():
    assert parse_audio_profile(PROFILE.projection()) == PROFILE
    assert "segmentation" not in AudioReceiveProfile().projection()
    for value in (None, True, "provider-vad", {}, "unbounded"):
        with pytest.raises(ValueError):
            parse_audio_profile(PROFILE.projection() | {"segmentation": value})


@pytest.mark.parametrize(
    "sequence,expected",
    [
        ([SPEECH] * 6 + [QUIET] * 24, 11),
        ([SPEECH] * 3 + [QUIET] * 27, 10),
        ([QUIET] * 30, 30),
        ([SPEECH] * 30, 30),
        ([SPEECH, QUIET] * 15, 30),
        ([struct.pack("<h", 599) * 1600] * 30, 30),
    ],
)
def test_energy_endpoint_has_bounded_onset_silence_minimum_and_maximum(sequence, expected):
    detector = segment_boundary(PROFILE)
    finished = None
    for index, pcm in enumerate(sequence):
        if detector.push(pcm):
            finished = index + 1
            break
    assert finished == expected
    assert not any(isinstance(value, (bytes, bytearray, list)) for value in vars(detector).values())
    with pytest.raises(ValueError):
        detector.push(QUIET)


def test_fixed_profile_does_not_silently_enable_energy_segmentation():
    detector = segment_boundary(replace(PROFILE, segmentation="fixed"))
    for index in range(30):
        assert detector.push(SPEECH if index < 3 else QUIET) is (index == 29)


@pytest.mark.parametrize("budget", [0, True, 1, 11, 101, 10.0])
@pytest.mark.parametrize("factory", [FixedSegment, EnergySegment])
def test_segment_port_rejects_nonprofile_budgets(factory, budget):
    with pytest.raises(ValueError):
        factory(budget)


@pytest.mark.parametrize("pcm", [None, bytearray(3200), b"", b"\0" * 3199, b"\0" * 6400])
def test_bad_packet_never_advances_segment(pcm):
    detector = EnergySegment(30)
    with pytest.raises(ValueError):
        detector.push(pcm)
    assert detector.window.chunks == 0


@pytest.mark.parametrize(
    "samples,allowed",
    [
        (16000, True),
        (17600, True),
        (48000, True),
        (0, False),
        (True, False),
        (14400, False),
        (16001, False),
        (49600, False),
    ],
)
def test_early_result_is_accepted_only_under_exact_energy_profile(samples, allowed):
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = PROFILE.projection()
    job = service.start(payload)["job"]
    result = payload | {
        "audio_task_id": job["task_id"],
        "audio_lease_id": job["lease_id"],
        "end_sample": samples,
        "language": "de",
        "text": "ephemeral",
    }
    if allowed:
        assert service.complete(result)["reply"] is None
    else:
        with pytest.raises(MeetError, match="result_invalid"):
            service.complete(result)


def test_fixed_hub_assignment_still_rejects_early_result_even_with_globally_valid_samples():
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = replace(PROFILE, segmentation="fixed").projection()
    job = service.start(payload)["job"]
    with pytest.raises(MeetError, match="result_invalid"):
        service.complete(
            payload
            | {
                "audio_task_id": job["task_id"],
                "audio_lease_id": job["lease_id"],
                "end_sample": 17600,
                "language": "de",
                "text": "ephemeral",
            }
        )


@pytest.mark.parametrize(
    "value",
    [None, {}, True, {"schema": "ananta.meet-audio-segment-probe.v1", "profile": "sample-boundary-v1", "supported": 1}],
)
def test_unavailable_or_malformed_probe_cannot_enable_early_reception(value):
    with pytest.raises(ValueError):
        require_segment_probe(value)


def test_finish_receipt_binds_exact_subscription_and_sample_boundary():
    value = {"schema": "ananta.meet-audio-segment-finished.v1", "subscriptionId": "a" * 32, "endSample": 17600}
    require_segment_finished(value, "a" * 32, 17600)
    for patch in ({"subscriptionId": "b" * 32}, {"endSample": 17600.0}, {"endSample": 16000}, {"extra": True}):
        with pytest.raises(ValueError):
            require_segment_finished(value | patch, "a" * 32, 17600)
