from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from voice_runtime.preprocessing.audio_decode import DecodedPcmAudio
from voice_runtime.preprocessing.vad_backends import (
    LocalSileroProbabilityProvider,
    PassThroughPcmVad,
    SileroPcmVad,
    VadSettings,
    WebRtcPcmVad,
    build_pcm_vad_processor,
)


def _pcm(*, duration_ms: int, timeline_start_ms: int = 0) -> DecodedPcmAudio:
    sample_rate = 16_000
    return DecodedPcmAudio(
        filename="sample.wav",
        pcm_s16le=b"\x00\x00" * (duration_ms * sample_rate // 1000),
        sample_rate_hz=sample_rate,
        duration_ms=duration_ms,
        source_format="wav",
        timeline_start_ms=timeline_start_ms,
    )


class _DecisionVad:
    def __init__(self, decisions):
        self._decisions = iter(decisions)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        assert len(frame) == sample_rate * 30 // 1000 * 2
        return next(self._decisions, False)


def test_passthrough_vad_preserves_absolute_timeline_and_pcm():
    audio = _pcm(duration_ms=120, timeline_start_ms=500)

    segments = PassThroughPcmVad().split(audio)

    assert len(segments) == 1
    assert segments[0].start_ms == 500
    assert segments[0].end_ms == 620
    assert segments[0].audio.pcm_s16le == audio.pcm_s16le


def test_webrtc_vad_emits_absolute_padded_speech_segments():
    decisions = [False, True, True, True, False, False, False, False]
    settings = VadSettings(frame_ms=30, padding_ms=30, min_speech_ms=60, min_silence_ms=60)
    audio = _pcm(duration_ms=240, timeline_start_ms=1_000)
    vad = WebRtcPcmVad(settings=settings, vad_factory=lambda _aggressiveness: _DecisionVad(decisions))

    segments = vad.split(audio)

    assert len(segments) == 1
    assert segments[0].start_ms == 1_000
    assert segments[0].end_ms == 1_150
    assert segments[0].audio.timeline_start_ms == 1_000
    assert segments[0].audio.duration_ms == 150


def test_webrtc_vad_enforces_max_segment_duration():
    decisions = [True] * 10
    settings = VadSettings(
        frame_ms=30,
        padding_ms=0,
        min_speech_ms=30,
        min_silence_ms=60,
        max_segment_ms=120,
    )
    vad = WebRtcPcmVad(settings=settings, vad_factory=lambda _aggressiveness: _DecisionVad(decisions))

    segments = vad.split(_pcm(duration_ms=300))

    assert [segment.end_ms - segment.start_ms for segment in segments] == [120, 120, 60]


def test_webrtc_vad_reports_missing_optional_dependency(monkeypatch):
    monkeypatch.setattr(
        "voice_runtime.preprocessing.vad_backends.importlib.import_module",
        lambda _name: (_ for _ in ()).throw(ModuleNotFoundError()),
    )

    with pytest.raises(RuntimeError, match="optional dependency"):
        WebRtcPcmVad().split(_pcm(duration_ms=30))


class _SileroProbabilities:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.frame_samples: int | None = None

    def probabilities(self, _audio, *, frame_samples: int) -> list[float]:
        self.frame_samples = frame_samples
        return self.values


def test_silero_vad_uses_512_sample_frames_threshold_and_absolute_timeline():
    provider = _SileroProbabilities([0.1, 0.85, 0.9, 0.2, 0.1, 0.1])
    settings = VadSettings(frame_ms=30, padding_ms=0, min_speech_ms=30, min_silence_ms=30)
    vad = SileroPcmVad(provider=provider, settings=settings, threshold=0.8)

    segments = vad.split(_pcm(duration_ms=192, timeline_start_ms=500))

    assert provider.frame_samples == 512
    assert [(segment.start_ms, segment.end_ms) for segment in segments] == [(530, 590)]
    assert segments[0].audio.timeline_start_ms == 530


def test_silero_threshold_is_configurable_and_sample_contract_is_strict():
    settings = VadSettings(frame_ms=30, padding_ms=0, min_speech_ms=30, min_silence_ms=30)
    audio = _pcm(duration_ms=64)
    assert SileroPcmVad(
        provider=_SileroProbabilities([0.6, 0.6]),
        settings=settings,
        threshold=0.5,
    ).split(audio)
    assert not SileroPcmVad(
        provider=_SileroProbabilities([0.6, 0.6]),
        settings=settings,
        threshold=0.8,
    ).split(audio)

    invalid_rate = DecodedPcmAudio(
        filename="sample.wav",
        pcm_s16le=b"\x00\x00" * 80,
        sample_rate_hz=8_000,
        duration_ms=10,
        source_format="wav",
    )
    with pytest.raises(ValueError, match="16kHz"):
        SileroPcmVad(provider=_SileroProbabilities([1.0])).split(invalid_rate)


def test_silero_local_loader_rejects_missing_unverified_and_pickle_style_paths(tmp_path, monkeypatch):
    imported = False

    def import_module(_name):
        nonlocal imported
        imported = True
        raise AssertionError("dependency must not load before local path validation")

    monkeypatch.setattr("voice_runtime.preprocessing.vad_backends.importlib.import_module", import_module)
    with pytest.raises(RuntimeError, match="not configured"):
        LocalSileroProbabilityProvider()._load_model()
    unsafe = tmp_path / "silero.pt"
    unsafe.write_bytes(b"pickle-like")
    with pytest.raises(RuntimeError, match="invalid"):
        LocalSileroProbabilityProvider(str(unsafe))._load_model()
    assert imported is False


def test_silero_builder_is_lazy_and_preserves_unavailable_baseline(tmp_path):
    missing = tmp_path / "missing.jit"
    processor = build_pcm_vad_processor(
        "silero",
        silero_model_path=str(missing),
        silero_threshold=0.7,
    )

    assert processor.name() == "silero"
    with pytest.raises(RuntimeError, match="not found"):
        processor.split(_pcm(duration_ms=64))
    assert PassThroughPcmVad().split(_pcm(duration_ms=64))


def test_silero_parallel_first_access_loads_verified_local_model_once(tmp_path, monkeypatch):
    model_path = tmp_path / "silero.jit"
    model_path.write_bytes(b"verified-by-production-manifest")
    load_calls = 0

    class Model:
        def eval(self):
            return self

    class Jit:
        @staticmethod
        def load(path: str, *, map_location: str):
            nonlocal load_calls
            assert path == str(model_path)
            assert map_location == "cpu"
            load_calls += 1
            return Model()

    class Torch:
        jit = Jit()

    monkeypatch.setattr(
        "voice_runtime.preprocessing.vad_backends.importlib.import_module",
        lambda name: Torch() if name == "torch" else None,
    )
    provider = LocalSileroProbabilityProvider(str(model_path))

    with ThreadPoolExecutor(max_workers=8) as executor:
        models = list(executor.map(lambda _: provider._load_model(), range(24)))

    assert all(model is models[0] for model in models)
    assert load_calls == 1
