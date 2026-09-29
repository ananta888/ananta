"""Shared avatar/image/clip NVENC policy and bounded failure behavior."""

import subprocess
from unittest.mock import Mock

import pytest
from PIL import Image

from worker.meet_media.video_frames import encode_frames


def encoder_fixture(monkeypatch, tmp_path, failure=None):
    encoder = Mock(side_effect=failure)
    verifier = Mock()
    monkeypatch.setattr("worker.meet_media.video_frames.subprocess.run", encoder)
    monkeypatch.setattr("worker.meet_media.video_frames.verify_encoded_media", verifier)
    (tmp_path / "avatar.mp4").write_bytes(b"synthetic-encoded-output")
    options = dict(
        frame_source=lambda _: Image.new("RGB", (256, 256)),
        require_current=Mock(),
    )
    return encoder, verifier, options


def test_nvenc_working_set_is_explicit_and_cannot_auto_expand_for_reordering(monkeypatch, tmp_path):
    encoder, verifier, options = encoder_fixture(monkeypatch, tmp_path)
    encode_frames(tmp_path / "audio.wav", 0.2, tmp_path, **options)
    command = encoder.call_args.args[0]
    expected = {
        "-surfaces": "4",
        "-rc-lookahead": "0",
        "-bf": "0",
        "-delay": "0",
        "-zerolatency": "1",
        "-multipass": "disabled",
        "-tune": "ull",
        "-c:v": "h264_nvenc",
        "-pix_fmt": "yuv420p",
        "-framerate": "12",
        "-video_size": "256x256",
    }
    for flag, value in expected.items():
        assert command.count(flag) == 1
        assert command[command.index(flag) + 1] == value
    assert encoder.call_args.kwargs == dict(check=True, timeout=30, capture_output=True)
    verifier.assert_called_once()


@pytest.mark.parametrize(
    "failure,code",
    [
        (FileNotFoundError("private/path"), "meet_video_encoder_unavailable_or_failed"),
        (subprocess.CalledProcessError(1, ["private"], stderr=b"secret"), "meet_video_encoder_unavailable_or_failed"),
        (subprocess.TimeoutExpired(["private"], 30, stderr=b"secret"), "meet_video_encoder_timeout"),
    ],
)
def test_encoder_errors_are_bounded_redacted_and_never_fall_back(monkeypatch, tmp_path, failure, code):
    encoder, verifier, options = encoder_fixture(monkeypatch, tmp_path, failure)
    with pytest.raises(ValueError) as raised:
        encode_frames(tmp_path / "audio.wav", 0.2, tmp_path, **options)
    assert str(raised.value) == code
    assert raised.value.__suppress_context__
    encoder.assert_called_once()
    verifier.assert_not_called()
