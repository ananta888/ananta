"""Closed clip receipts and bounded, revocable CPU decoder orchestration."""

import base64
import hashlib
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from ananta_contracts.persona_video import (
    MAX_INPUT_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_VIDEO_BYTES,
    SanitizedPersonaVideo,
    decode_video,
    encode_video,
)
from worker.meet_media.persona_video_inspector import PersonaVideoInspector
from worker.meet_media.persona_video_probe import inspect_video_probe


def clip():
    preview = io.BytesIO()
    Image.new("RGBA", (256, 256), "red").save(preview, format="PNG")
    video, png = b"0000ftyp00000000", preview.getvalue()
    return SanitizedPersonaVideo(
        hashlib.sha256(b"source").hexdigest(),
        hashlib.sha256(video).hexdigest(),
        hashlib.sha256(png).hexdigest(),
        12,
        video,
        png,
    )


def probe():
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 256,
                "height": 256,
                "r_frame_rate": "12/1",
                "nb_read_frames": "12",
            }
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.000000"},
    }


def test_closed_receipt_roundtrip_does_not_expose_payloads_in_repr():
    value = clip()
    assert decode_video(encode_video(value), value.source_sha256) == value
    assert value.duration_ms == 1000 and "ftyp" not in repr(value)
    assert encode_video(value)["preview"] == base64.b64encode(value.preview).decode()


@pytest.mark.parametrize(
    "key,value",
    [
        ("extra", True),
        ("schema", "other"),
        ("profile", "other"),
        ("frames", True),
        ("frames", 121),
        ("frames", 1),
        ("source_sha256", "0" * 64),
        ("video_sha256", "0" * 64),
        ("preview_sha256", "0" * 64),
        ("video", "!"),
        ("preview", "a" * (4 * ((MAX_PREVIEW_BYTES + 2) // 3) + 1)),
    ],
)
def test_receipt_rejects_mutations(key, value):
    original = clip()
    with pytest.raises(ValueError):
        decode_video(encode_video(original) | {key: value}, original.source_sha256)


def test_profile_allows_source_aac_but_never_normalized_audio():
    value = probe()
    assert inspect_video_probe(value, normalized=True) == 12
    value["streams"].append({"codec_type": "audio", "codec_name": "aac", "channels": 1, "sample_rate": "22050"})
    assert inspect_video_probe(value) == 12
    with pytest.raises(ValueError):
        inspect_video_probe(value, normalized=True)
    value["streams"][1]["sample_rate"] = []
    with pytest.raises(ValueError):
        inspect_video_probe(value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("codec_name", "vp9"),
        ("codec_type", "attachment"),
        ("width", 1281),
        ("height", True),
        ("height", 721),
        ("r_frame_rate", "0/0"),
        ("r_frame_rate", "31/1"),
        ("r_frame_rate", []),
        ("nb_read_frames", "301"),
        ("nb_read_frames", "1"),
    ],
)
def test_unsupported_source_streams_fail_closed(key, value):
    data = probe()
    data["streams"][0][key] = value
    with pytest.raises(ValueError):
        inspect_video_probe(data)


@pytest.mark.parametrize("duration", ["NaN", "11", "0", "-1", [], "999999999"])
def test_duration_is_bounded_before_conversion(duration):
    data = probe()
    data["format"]["duration"] = duration
    with pytest.raises(ValueError):
        inspect_video_probe(data)


def test_normalized_extent_matches_actual_frame_count():
    data = probe()
    data["format"]["duration"] = "2.0"
    assert inspect_video_probe(data) == 12
    with pytest.raises(ValueError, match="extent"):
        inspect_video_probe(data, normalized=True)


def scripted_runner():
    value = clip()
    outputs = [json.dumps(probe()).encode(), value.video, json.dumps(probe()).encode(), value.preview]
    runner = Mock()
    runner.run.side_effect = [SimpleNamespace(returncode=0, stdout=output) for output in outputs]
    return runner


def test_inspector_uses_only_bounded_pipe_input_and_rechecks_authority():
    runner, current = scripted_runner(), Mock()
    value = PersonaVideoInspector(
        require_current=current, deadline_monotonic=time.monotonic() + 25, runner=runner
    ).inspect(clip().video, "video/mp4")
    assert value.frames == 12 and value.source_sha256 == hashlib.sha256(clip().video).hexdigest()
    assert runner.run.call_count == 4 and current.call_count == 9
    for call, maximum in zip(
        runner.run.call_args_list, (131072, MAX_VIDEO_BYTES, 131072, MAX_PREVIEW_BYTES), strict=True
    ):
        argv, options = call.args[0], call.kwargs
        assert argv[argv.index("-protocol_whitelist") + 1] == "pipe"
        assert argv[argv.index("-i") + 1] == "pipe:0"
        assert options["max_stdout_bytes"] == maximum
        assert 0 < options["timeout_seconds"] <= 8 and options["cancellation_check"] is current
    normalize = runner.run.call_args_list[1].args[0]
    assert "-an" in normalize and normalize[normalize.index("-frames:v") + 1] == "120"


@pytest.mark.parametrize("phase", range(9))
def test_revocation_at_every_boundary_never_returns_a_clip(phase):
    runner, current = scripted_runner(), Mock()
    current.side_effect = [None] * phase + [PermissionError("private context")]
    inspector = PersonaVideoInspector(require_current=current, deadline_monotonic=time.monotonic() + 25, runner=runner)
    with pytest.raises(ValueError, match="^persona_video_inspection_failed_or_revoked$"):
        inspector.inspect(clip().video, "video/mp4")
    assert runner.run.call_count == min(4, (phase + 1) // 2)


@pytest.mark.parametrize(
    "content,mime",
    [(b"bad", "video/mp4"), (b"x" * (MAX_INPUT_BYTES + 1), "video/mp4"), (b"0000ftyp00000000", "video/webm")],
)
def test_invalid_input_never_starts_decoder(content, mime):
    runner = Mock()
    with pytest.raises(ValueError, match="input_invalid"):
        PersonaVideoInspector(require_current=Mock(), deadline_monotonic=time.monotonic() + 25, runner=runner).inspect(
            content, mime
        )
    runner.run.assert_not_called()


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), 0])
def test_invalid_deadline_rejected(deadline):
    with pytest.raises(ValueError, match="deadline_invalid"):
        PersonaVideoInspector(require_current=Mock(), deadline_monotonic=deadline)


def test_expired_deadline_and_decoder_failure_are_sanitized(monkeypatch):
    now = time.monotonic()
    runner = Mock()
    inspector = PersonaVideoInspector(require_current=Mock(), deadline_monotonic=now + 25, runner=runner)
    monkeypatch.setattr("worker.meet_media.persona_video_processes.time.monotonic", lambda: now + 26)
    with pytest.raises(ValueError, match="failed_or_revoked"):
        inspector.inspect(clip().video, "video/mp4")
    runner.run.assert_not_called()


@pytest.mark.parametrize(
    "result", [SimpleNamespace(returncode=1, stdout=b"secret"), SimpleNamespace(returncode=0, stdout=b"secret")]
)
def test_decoder_failure_never_leaks_output(result):
    runner = Mock(run=Mock(return_value=result))
    inspector = PersonaVideoInspector(require_current=Mock(), deadline_monotonic=time.monotonic() + 25, runner=runner)
    with pytest.raises(ValueError, match="^persona_video_inspection_failed_or_revoked$"):
        inspector.inspect(clip().video, "video/mp4")
    assert runner.run.call_count == 1
