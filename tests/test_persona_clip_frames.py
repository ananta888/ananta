"""Bounded decoded frame lifecycle, explicit repetition and visible source labels."""

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_persona_video_inspection import clip, probe
from worker.meet_media.persona_clip_frames import FRAME_BYTES, PersonaClipFrames, render_persona_clip


def setup_frames(**overrides):
    value = replace(clip(), frames=2)
    metadata = probe()
    metadata["streams"][0]["nb_read_frames"] = "2"
    metadata["format"]["duration"] = "0.166667"
    raw = b"\xff\0\0" * (256 * 256) + b"\0\xff\0" * (256 * 256)
    runner = Mock()
    runner.run.side_effect = [
        SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode()),
        SimpleNamespace(returncode=0, stdout=raw),
    ]
    options = dict(
        origin_kind="upload",
        classification="test_only",
        repeat_mode="loop",
        require_current=Mock(),
        deadline_monotonic=time.monotonic() + 25,
        runner=runner,
    )
    options.update(overrides)
    return value, options, runner


def test_repeat_is_explicit_and_opaque_label_cannot_be_hidden_by_source():
    value, options, runner = setup_frames()
    frames = PersonaClipFrames(value, **options)
    first, second = frames.frame(0), frames.frame(1)
    assert first.getpixel((0, 0)) == (255, 0, 0) and second.getpixel((0, 0)) == (0, 255, 0)
    assert frames.frame(2).tobytes() == first.tobytes()
    assert frames.label == "ANANTA | AI | IMPORTED | TEST"
    assert first.getpixel((0, 255)) == (16, 28, 48)
    assert first.crop((0, 222, 256, 256)).tobytes() != b"\x10\x1c\x30" * (256 * 34)
    options_call = runner.run.call_args.kwargs
    assert options_call["max_stdout_bytes"] == 2 * FRAME_BYTES
    assert options_call["timeout_seconds"] == 5
    frames.close()
    frames.close()
    with pytest.raises(ValueError, match="closed"):
        frames.frame(0)


def test_hold_last_never_silently_loops_and_generated_label_is_distinct():
    value, options, _ = setup_frames(repeat_mode="hold_last", origin_kind="generated", classification="synthetic")
    frames = PersonaClipFrames(value, **options)
    assert frames.frame(2).tobytes() == frames.frame(1).tobytes()
    assert frames.label == "ANANTA | AI | GENERATED | SYNTH"
    frames.close()


@pytest.mark.parametrize(
    "options",
    [
        dict(repeat_mode="auto"),
        dict(repeat_mode=None),
        dict(origin_kind="camera"),
        dict(classification=[]),
        dict(origin_kind="generated", classification="production"),
    ],
)
def test_implicit_repeat_or_invalid_classification_never_decodes(options):
    value, kwargs, runner = setup_frames(**options)
    with pytest.raises(ValueError):
        PersonaClipFrames(value, **kwargs)
    runner.run.assert_not_called()


@pytest.mark.parametrize("index", [-1, 480, True, "1"])
def test_frame_requests_obey_maximum_turn_budget(index):
    value, options, _ = setup_frames()
    frames = PersonaClipFrames(value, **options)
    with pytest.raises(ValueError, match="index_invalid"):
        frames.frame(index)
    frames.close()


def test_revoked_frame_read_returns_no_pixels():
    value, options, _ = setup_frames()
    frames = PersonaClipFrames(value, **options)
    options["require_current"].side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        frames.frame(0)
    frames.close()


@pytest.mark.parametrize("failure", ["metadata_count", "raw_count", "process"])
def test_actual_decode_must_match_pinned_count(failure):
    value, options, runner = setup_frames()
    replies = list(runner.run.side_effect)
    if failure == "metadata_count":
        value = replace(value, frames=3)
    elif failure == "raw_count":
        replies[1].stdout = replies[1].stdout[:-1]
    else:
        replies[1].returncode = 1
    runner.run.side_effect = replies
    with pytest.raises(ValueError, match="decode_failed_or_revoked"):
        PersonaClipFrames(value, **options)


@pytest.mark.parametrize("fails", [False, True])
def test_render_closes_decoded_bytes_on_success_or_encoder_failure(monkeypatch, tmp_path, fails):
    value, options, _ = setup_frames()
    actual = PersonaClipFrames(value, **options)
    constructor = Mock(return_value=actual)
    monkeypatch.setattr("worker.meet_media.persona_clip_frames.PersonaClipFrames", constructor)
    encoder = Mock(return_value=tmp_path / "result.mp4")
    if fails:
        encoder.side_effect = ValueError("codec unavailable")
    monkeypatch.setattr("worker.meet_media.video_frames.encode_frames", encoder)
    if fails:
        with pytest.raises(ValueError):
            render_persona_clip(value, tmp_path / "audio.wav", 1, tmp_path, **options)
    else:
        assert render_persona_clip(value, tmp_path / "audio.wav", 1, tmp_path, **options) == tmp_path / "result.mp4"
    assert actual._raw is None
    assert encoder.call_args.kwargs["require_current"] is options["require_current"]


@pytest.mark.parametrize("duration", [0, -1, 41, True, float("nan"), float("inf")])
def test_invalid_turn_duration_fails_before_decode(tmp_path, duration):
    value, options, runner = setup_frames()
    with pytest.raises(ValueError, match="duration_invalid"):
        render_persona_clip(value, tmp_path / "audio.wav", duration, tmp_path, **options)
    runner.run.assert_not_called()
