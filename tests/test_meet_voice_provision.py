"""Deterministic model-only installation; no network, keys or service mutation."""

import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_voice_catalog import VoiceModel, VoicePreset
from scripts import provision_meet_voice as provision


def fixture(monkeypatch, tmp_path):
    model_bytes, config_bytes = b"synthetic model", b"{}"
    model = VoiceModel(
        "fixture.onnx",
        "synthetic/path",
        "a" * 40,
        hashlib.sha256(model_bytes).hexdigest(),
        hashlib.sha256(config_bytes).hexdigest(),
        20,
        10,
    )
    preset = VoicePreset("synthetic", "de-DE", model)
    def lookup(voice):
        if voice != "synthetic":
            raise ValueError("synthetic_voice_unknown")
        return preset

    monkeypatch.setattr(provision, "voice_preset", lookup)
    directory = tmp_path / "models"
    fetch = Mock(side_effect=lambda url, _maximum: config_bytes if url.endswith(".json") else model_bytes)
    return SimpleNamespace(**locals())


def test_model_only_installer_is_hash_checked_idempotent_and_does_not_create_keys(monkeypatch, tmp_path):
    f = fixture(monkeypatch, tmp_path)
    result = provision.provision_voice(f.directory, "synthetic", fetch=f.fetch)
    assert result == {"voice_id": "synthetic", "model": "fixture.onnx", "verified": True}
    assert sorted(path.name for path in f.directory.iterdir()) == ["fixture.onnx", "fixture.onnx.json"]
    assert f.fetch.call_count == 2
    assert all(
        call.args[0].startswith("https://huggingface.co/rhasspy/piper-voices/resolve/" + "a" * 40)
        for call in f.fetch.call_args_list
    )
    for path in f.directory.iterdir():
        assert path.stat().st_mode & 0o077 == 0 and path.stat().st_nlink == 1
    assert provision.provision_voice(f.directory, "synthetic", fetch=f.fetch) == result
    assert f.fetch.call_count == 2


@pytest.mark.parametrize("change", ["bytes", "oversize", "symlink", "hardlink", "concurrent"])
def test_invalid_or_existing_foreign_model_is_never_overwritten(monkeypatch, tmp_path, change):
    f = fixture(monkeypatch, tmp_path)
    f.directory.mkdir()
    target = f.directory / f.model.name
    if change in {"bytes", "oversize"}:
        f.fetch.side_effect = None
        f.fetch.return_value = b"wrong" if change == "bytes" else b"x" * 21
    elif change == "symlink":
        other = tmp_path / "other"
        other.write_bytes(b"must remain")
        target.symlink_to(other)
    elif change == "hardlink":
        other = tmp_path / "other"
        other.write_bytes(f.model_bytes)
        target.hardlink_to(other)
    else:

        def race(_url, _maximum):
            target.write_bytes(b"concurrent owner")
            return f.model_bytes

        f.fetch.side_effect = race
    with pytest.raises(ValueError):
        provision.provision_voice(f.directory, "synthetic", fetch=f.fetch)
    assert not list(f.directory.glob(".voice-download-*"))
    if change in {"bytes", "oversize"}:
        assert not target.exists()
    elif change == "concurrent":
        assert target.read_bytes() == b"concurrent owner"
    elif change == "symlink":
        assert other.read_bytes() == b"must remain" and target.is_symlink()


def test_unknown_voice_and_symlink_directory_do_not_create_or_modify_files(monkeypatch, tmp_path):
    f = fixture(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        provision.provision_voice(f.directory, "unknown", fetch=f.fetch)
    assert not f.directory.exists()
    actual = tmp_path / "actual"
    actual.mkdir()
    f.directory.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="directory_invalid"):
        provision.provision_voice(f.directory, "synthetic", fetch=f.fetch)
    f.fetch.assert_not_called()
    assert not list(actual.iterdir())


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


@pytest.mark.parametrize("case", ["ok", "length", "encoding", "oversize", "truncated", "status", "deadline"])
def test_download_enforces_response_size_encoding_status_and_total_budget(monkeypatch, case):
    headers = {"Content-Length": "3"}
    if case == "length":
        headers["Content-Length"] = "99"
    elif case == "encoding":
        headers["Content-Encoding"] = "gzip"
    elif case == "truncated":
        headers["Content-Length"] = "4"
    elif case == "oversize":
        headers = {}
    response = Response(
        b"x" * (11 if case == "oversize" else 3), status=503 if case == "status" else 200, headers=headers
    )
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(provision.urllib.request, "build_opener", lambda *_args: opener)
    ticks = iter([0, 0, 91]) if case == "deadline" else None
    def clock():
        return next(ticks) if ticks is not None else 0
    if case == "ok":
        assert provision.download("https://huggingface.co/fixed", 10, clock=clock) == b"xxx"
    else:
        with pytest.raises(ValueError):
            provision.download("https://huggingface.co/fixed", 10, clock=clock)
    assert response.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/file",
        "https://127.0.0.1/file",
        "https://foreign.test/file",
        "https://user:pass@huggingface.co/file",
        "https://huggingface.co:8443/file",
    ],
)
def test_download_redirects_cannot_access_arbitrary_destinations(url):
    with pytest.raises(ValueError, match="redirect_denied"):
        provision.VoiceDownloadRedirect().redirect_request(None, None, 302, "", {}, url)
