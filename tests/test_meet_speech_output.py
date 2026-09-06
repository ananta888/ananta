"""Headless speech framing, cancellation and provider-boundary regressions."""

import wave
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from worker.meet_media.audio_output import FRAME_SAMPLES, SAMPLE_RATE, speech_frames
from worker.meet_media.piper_speech import PiperSpeechSource
from worker.meet_media.speech import speech


class Source:
    def __init__(self, blocks):
        self.blocks, self.closed, self.pulls = blocks, False, 0

    def synthesize(self, text, *, max_samples, require_current):
        try:
            for block in self.blocks:
                self.pulls += 1
                yield block
        finally:
            self.closed = True


def collect(source, **kwargs):
    return list(speech_frames("Hallo", source, require_current=lambda: None, **kwargs))


def test_sample_clock_crosses_sentence_boundaries_without_padding():
    pcm = np.arange(2301, dtype="<i2").tobytes()
    source = Source([pcm[:602], pcm[602:2000], pcm[2000:]])
    frames = collect(source)
    assert b"".join(frame.pcm_s16le for frame in frames) == pcm
    assert [frame.start_sample for frame in frames] == list(range(0, 2301, FRAME_SAMPLES))
    assert [frame.timestamp_us for frame in frames] == list(range(0, 120_000, 20_000))
    assert all(frame.samples == FRAME_SAMPLES for frame in frames[:-1])
    assert frames[-1].samples == 96
    assert source.closed
    assert "pcm_s16le" not in repr(frames[0])


def test_pull_backpressure_and_close_never_generate_the_next_sentence():
    source = Source([b"\x01\x00" * 1000, b"\x02\x00" * 1000])
    frames = speech_frames("Hallo", source, require_current=lambda: None)
    assert next(frames).start_sample == 0
    assert source.pulls == 1
    frames.close()
    assert source.pulls == 1 and source.closed
    with pytest.raises(StopIteration):
        next(frames)


def test_revocation_discards_pending_audio_and_closes_provider():
    revoked = False

    def checkpoint():
        if revoked:
            raise PermissionError("revoked")

    source = Source([b"\0\0" * 1000, b"\0\0" * 1000])
    frames = speech_frames("Hallo", source, require_current=checkpoint)
    next(frames)
    revoked = True
    with pytest.raises(PermissionError, match="revoked"):
        next(frames)
    assert source.closed and source.pulls == 1
    with pytest.raises(StopIteration):
        next(frames)


@pytest.mark.parametrize("block", [b"", b"x", "not-pcm", bytearray(b"\0\0"), None])
def test_invalid_provider_blocks_fail_closed(block):
    source = Source([block])
    with pytest.raises(ValueError, match="pcm_invalid"):
        collect(source)
    assert source.closed


def test_duration_limit_is_checked_before_the_overbudget_block_is_emitted():
    source = Source([b"\0\0" * SAMPLE_RATE, b"\0\0"])
    frames = speech_frames("Hallo", source, max_seconds=1, require_current=lambda: None)
    assert sum(next(frames).samples for _ in range(50)) == SAMPLE_RATE
    with pytest.raises(ValueError, match="duration_exceeded"):
        next(frames)
    assert source.closed


@pytest.mark.parametrize("seconds", [0, 41, True, 1.0, "1"])
def test_invalid_budget_is_rejected_before_provider_invocation(seconds):
    source = Mock()
    with pytest.raises(ValueError, match="budget_invalid"):
        collect(source, max_seconds=seconds)
    source.synthesize.assert_not_called()


@pytest.mark.parametrize("text", ["", " ", "a" * 451, "hello\0world", None])
def test_text_is_bounded_before_provider_invocation(text):
    source = Mock()
    with pytest.raises(ValueError, match="text_invalid"):
        list(speech_frames(text, source, require_current=lambda: None))
    source.synthesize.assert_not_called()


def test_empty_synthesis_fails_and_failed_wav_is_removed(tmp_path):
    path = tmp_path / "speech.wav"
    with pytest.raises(ValueError, match="speech_empty"):
        speech("Hallo", path, source=Source([]))
    assert not path.exists()


def test_wav_consumer_preserves_exact_samples_and_existing_files(tmp_path):
    path = tmp_path / "speech.wav"
    pcm = np.arange(1234, dtype="<i2").tobytes()
    samples, rate, duration = speech("Hallo", path, source=Source([pcm]))
    assert rate == SAMPLE_RATE and duration == 1234 / SAMPLE_RATE
    assert len(samples) == 1234
    with wave.open(str(path), "rb") as output:
        assert output.getnchannels() == 1 and output.getsampwidth() == 2
        assert output.readframes(1234) == pcm
    original = path.read_bytes()
    source = Mock()
    with pytest.raises(FileExistsError):
        speech("Hallo", path, source=source)
    assert path.read_bytes() == original
    source.synthesize.assert_not_called()


def test_wav_write_failure_closes_generator_and_removes_partial_output(tmp_path, monkeypatch):
    source = Source([b"\0\0" * 1000])
    monkeypatch.setattr(wave.Wave_write, "writeframesraw", Mock(side_effect=OSError("disk_full")))
    path = tmp_path / "speech.wav"
    with pytest.raises(OSError, match="disk_full"):
        speech("Hallo", path, source=source)
    assert source.closed and not path.exists()


def test_overbudget_wav_is_never_returned_as_a_truncated_success(tmp_path):
    path = tmp_path / "speech.wav"
    source = Source([b"\0\0" * SAMPLE_RATE, b"\0\0"])
    with pytest.raises(ValueError, match="duration_exceeded"):
        speech("Hallo", path, source=source, max_seconds=1)
    assert source.closed and not path.exists()


def test_denied_initial_checkpoint_does_not_load_a_model_or_leave_a_wav(tmp_path):
    path = tmp_path / "speech.wav"
    source = Mock()
    with pytest.raises(PermissionError, match="revoked"):
        speech("Hallo", path, source=source, require_current=Mock(side_effect=PermissionError("revoked")))
    source.synthesize.assert_not_called()
    assert not path.exists()


def chunk(**changes):
    return SimpleNamespace(
        **(
            dict(
                sample_rate=SAMPLE_RATE,
                sample_width=2,
                sample_channels=1,
                audio_float_array=np.array([-1, 0, 1], dtype=np.float32),
            )
            | changes
        )
    )


def test_piper_adapter_emits_explicit_little_endian_pcm():
    voice = Mock()
    voice.synthesize.return_value = iter([chunk()])
    source = PiperSpeechSource(loader=lambda: voice)
    assert b"".join(frame.pcm_s16le for frame in collect(source)) == b"\x01\x80\0\0\xff\x7f"
    voice.synthesize.assert_called_once_with("Hallo")


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_rate": 48_000},
        {"sample_width": 4},
        {"sample_channels": 2},
        {"audio_float_array": np.array([float("nan")], dtype=np.float32)},
        {"audio_float_array": np.array([float("inf")], dtype=np.float32)},
        {"audio_float_array": np.array([1.1], dtype=np.float32)},
        {"audio_float_array": np.array([1], dtype=np.float64)},
        {"audio_float_array": np.array([[1]], dtype=np.float32)},
        {"audio_float_array": np.array([], dtype=np.float32)},
        {"audio_float_array": np.zeros(40 * SAMPLE_RATE + 1, dtype=np.float32)},
    ],
)
def test_piper_adapter_rejects_bad_format_samples_and_excess_duration(changes):
    voice = Mock()
    voice.synthesize.return_value = iter([chunk(**changes)])
    with pytest.raises(ValueError):
        collect(PiperSpeechSource(loader=lambda: voice))


def test_provider_revocation_during_inference_discards_entire_result():
    revoked = False

    def synthesize(text):
        nonlocal revoked
        revoked = True
        yield chunk()

    def checkpoint():
        if revoked:
            raise PermissionError("revoked")

    source = PiperSpeechSource(loader=lambda: SimpleNamespace(synthesize=synthesize))
    with pytest.raises(PermissionError, match="revoked"):
        next(speech_frames("Hallo", source, require_current=checkpoint))
