"""Deterministic decoded-clock negatives and bounded decoder invocation."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.av_quality import validate_decoded_media, verify_encoded_media


def decoded():
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 256,
                "height": 256,
                "r_frame_rate": "12/1",
            },
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "22050", "channels": 1},
        ],
        "frames": [
            {
                "media_type": "video",
                "stream_index": 0,
                "width": 256,
                "height": 256,
                "best_effort_timestamp_time": f"{index / 12:.6f}",
            }
            for index in range(12)
        ]
        + [
            {
                "media_type": "audio",
                "stream_index": 1,
                "nb_samples": 1024,
                "best_effort_timestamp_time": f"{index * 1024 / 22050:.6f}",
            }
            for index in range(22)
        ],
    }


def test_decoded_clocks_measure_codec_padding_without_claiming_live_delivery():
    report = validate_decoded_media(decoded(), expected_samples=22050)
    assert report["video_frames_decoded"] == 12 and report["audio_frames_decoded"] == 22
    assert report["audio_padding_samples"] == 478 and report["end_skew_us"] == 21678
    assert report["live_delivery_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        "missing_audio",
        "wrong_codec",
        "oversize",
        "fps",
        "rate",
        "stereo",
        "video_gap",
        "video_repeat",
        "audio_gap",
        "bad_pts",
        "foreign_frame",
        "missing_video",
        "padding",
        "huge_frames",
    ],
)
def test_invalid_streams_or_timestamps_are_not_quality_success(change):
    probe = decoded()
    if change == "missing_audio":
        probe["streams"].pop()
    elif change == "wrong_codec":
        probe["streams"][0]["codec_name"] = "vp9"
    elif change == "oversize":
        probe["streams"][0]["width"] = 4096
    elif change == "fps":
        probe["streams"][0]["r_frame_rate"] = "60/1"
    elif change == "rate":
        probe["streams"][1]["sample_rate"] = "48000"
    elif change == "stereo":
        probe["streams"][1]["channels"] = 2
    elif change in ("video_gap", "video_repeat", "bad_pts"):
        probe["frames"][1]["best_effort_timestamp_time"] = {
            "video_gap": "0.2",
            "video_repeat": "0.0",
            "bad_pts": "NaN",
        }[change]
    elif change == "audio_gap":
        probe["frames"][-1]["best_effort_timestamp_time"] = "8.0"
    elif change == "foreign_frame":
        probe["frames"][0]["stream_index"] = 3
    elif change == "missing_video":
        probe["frames"].pop(11)
    elif change == "padding":
        probe["frames"].append(
            deepcopy(probe["frames"][-1]) | {"best_effort_timestamp_time": f"{22 * 1024 / 22050:.6f}"}
        )
    else:
        probe["frames"] *= 100
    with pytest.raises(ValueError):
        validate_decoded_media(probe, expected_samples=22050)


@pytest.mark.parametrize("count", [True, 0, -1, 882001, 1.5])
def test_invalid_audio_budget_never_starts_a_decoder(count):
    runner = Mock()
    with pytest.raises(ValueError):
        verify_encoded_media(b"0000ftyp00000000", expected_samples=count, require_current=Mock(), runner=runner)
    runner.run.assert_not_called()


def test_decoder_uses_only_bounded_stdin_and_rechecks_current_authority():
    runner, checkpoint = Mock(), Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(decoded()).encode())
    report = verify_encoded_media(
        b"0000ftyp00000000", expected_samples=22050, require_current=checkpoint, runner=runner
    )
    assert report["video_frames_decoded"] == 12
    argv = runner.run.call_args.args[0]
    options = runner.run.call_args.kwargs
    assert argv[argv.index("-protocol_whitelist") + 1] == "pipe"
    assert argv[argv.index("-i") + 1] == "pipe:0"
    assert options["max_stdout_bytes"] == 2 * 1024 * 1024 and options["timeout_seconds"] == 5
    assert options["cancellation_check"] is checkpoint and checkpoint.call_count == 3
    checkpoint.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        verify_encoded_media(b"0000ftyp00000000", expected_samples=22050, require_current=checkpoint, runner=runner)
    assert runner.run.call_count == 1


def test_failed_or_malformed_decoder_output_never_returns_quality():
    for result in (
        SimpleNamespace(returncode=1, stdout=b"{}"),
        SimpleNamespace(returncode=0, stdout=b"private garbage"),
    ):
        with pytest.raises(ValueError):
            verify_encoded_media(
                b"0000ftyp00000000",
                expected_samples=22050,
                require_current=Mock(),
                runner=Mock(run=Mock(return_value=result)),
            )
