"""Pinned local voice inputs and strict shared profile; no live files/models."""

import hashlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_speech import (
    CONFIG_SHA256,
    MODEL_NAME,
    MODEL_SHA256,
    speech_profile,
    validate_speech_profile,
)
from worker.meet_media.piper_assets import load_pinned_assets, read_pinned_file


@pytest.mark.parametrize(
    "change",
    [
        {"voice_id": "another-speaker"},
        {"language": "en-US"},
        {"model_sha256": "0" * 64},
        {"config_sha256": "0" * 64},
        {"sample_rate": 48000},
        {"channels": True},
        {"sample_rate": 22050.0},
        {"max_seconds": True},
        {"max_seconds": 0},
        {"max_seconds": 41},
        {"url": "https://example.invalid/model"},
        {"model_revision": "main"},
    ],
)
def test_closed_profile_never_accepts_unapproved_voice_format_or_model(change):
    with pytest.raises(ValueError):
        validate_speech_profile(speech_profile() | change)


def test_profiles_are_independent_and_match_the_standalone_provisioner():
    from scripts.setup_meet_media import FILES

    first = speech_profile(max_seconds=5)
    assert validate_speech_profile(first) == first
    first["max_seconds"] = 1
    assert speech_profile()["max_seconds"] == 40
    assert FILES[MODEL_NAME] == MODEL_SHA256
    assert FILES[MODEL_NAME + ".json"] == CONFIG_SHA256


def test_pinned_snapshot_is_not_replaced_after_validation(tmp_path):
    path = tmp_path / "model.onnx"
    content = b"synthetic immutable model bytes"
    path.write_bytes(content)
    snapshot = read_pinned_file(path, sha256=hashlib.sha256(content).hexdigest(), maximum=100)
    path.write_bytes(b"replacement")
    assert snapshot == content
    with pytest.raises(ValueError, match="asset_changed"):
        read_pinned_file(path, sha256=hashlib.sha256(content).hexdigest(), maximum=100)


@pytest.mark.parametrize("kind", ["empty", "oversize", "symlink", "hardlink", "fifo"])
def test_invalid_local_inputs_are_bounded_and_never_followed(tmp_path, kind):
    import os

    path = tmp_path / "model.onnx"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = tmp_path / "private-other-file"
        target.write_bytes(b"private")
        path.symlink_to(target)
    else:
        path.write_bytes(b"" if kind == "empty" else b"x" * 50)
        if kind == "hardlink":
            (tmp_path / "linked-model").hardlink_to(path)
    with pytest.raises(ValueError):
        read_pinned_file(path, sha256="0" * 64, maximum=49 if kind == "oversize" else 100)


def test_mutated_provisioned_file_fails_before_any_model_parser(tmp_path, monkeypatch):
    model = tmp_path / MODEL_NAME
    model.write_bytes(b"tampered-model")
    monkeypatch.setenv("MEET_PIPER_MODEL", str(model))
    with pytest.raises(ValueError, match="asset_changed"):
        load_pinned_assets()
    monkeypatch.setenv("MEET_PIPER_MODEL", "relative.onnx")
    with pytest.raises(ValueError, match="path_invalid"):
        load_pinned_assets()


def test_cuda_loader_passes_only_verified_byte_snapshots_to_onnx(monkeypatch):
    from worker.meet_media import piper_speech

    session = Mock()
    session.get_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    ort = SimpleNamespace(
        preload_dlls=Mock(),
        get_available_providers=lambda: ["CUDAExecutionProvider"],
        SessionOptions=Mock(),
        InferenceSession=Mock(return_value=session),
    )
    voice = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=voice))
    monkeypatch.setitem(
        sys.modules,
        "piper.config",
        SimpleNamespace(
            PiperConfig=SimpleNamespace(from_dict=lambda _: SimpleNamespace(sample_rate=22050)),
        ),
    )
    monkeypatch.setattr(piper_speech, "load_pinned_assets", lambda _: (b"verified-bytes", b"{}"))
    result = piper_speech.load_cuda_voice()
    assert ort.InferenceSession.call_args.args == (b"verified-bytes",)
    session.disable_fallback.assert_called_once()
    assert result.use_tashkeel is False  # This fixed German profile needs no dynamic Arabic model download.
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    with pytest.raises(ValueError, match="cuda_unavailable"):
        piper_speech.load_cuda_voice()
    assert ort.InferenceSession.call_count == 1
