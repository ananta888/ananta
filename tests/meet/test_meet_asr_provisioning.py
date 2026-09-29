"""Deterministic model admission and isolated subprocess environment checks."""

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from scripts import setup_meet_asr
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media import asr_model

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def manifest(monkeypatch):
    content = b"deterministic synthetic model"
    files = {"model.bin": (len(content), hashlib.sha256(content).hexdigest())}
    monkeypatch.setattr(asr_model, "FILES", files)
    monkeypatch.setattr(setup_meet_asr, "FILES", files)
    return content


def test_provision_is_idempotent_and_checks_existing_content(tmp_path, monkeypatch, manifest):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(setup_meet_asr.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(manifest))
    setup_meet_asr.provision(tmp_path)
    monkeypatch.setattr(
        setup_meet_asr.urllib.request, "urlopen", lambda *_, **__: pytest.fail("unexpected re-download")
    )
    setup_meet_asr.provision(tmp_path)
    model = tmp_path / "models/faster-whisper-small/model.bin"
    model.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="model_file_invalid"):
        setup_meet_asr.provision(tmp_path)
    assert model.read_bytes() == b"invalid"


def test_integrity_failure_does_not_publish_partial_model(tmp_path, monkeypatch, manifest):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(setup_meet_asr.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(b"wrong"))
    with pytest.raises(ValueError, match="integrity_failed"):
        setup_meet_asr.provision(tmp_path)
    assert not (tmp_path / "models/faster-whisper-small").exists()
    assert not list((tmp_path / "models").iterdir())


def test_extra_files_or_links_are_not_admitted(tmp_path, manifest):
    (tmp_path / "model.bin").write_bytes(manifest)
    asr_model.verify_model(tmp_path)
    (tmp_path / "unexpected.json").write_text("{}")
    with pytest.raises(ValueError, match="files_invalid"):
        asr_model.verify_model(tmp_path)


def test_model_directory_symlink_is_rejected(tmp_path, manifest):
    target = tmp_path / "target"
    target.mkdir()
    (target / "model.bin").write_bytes(manifest)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="model_unavailable"):
        asr_model.verify_model(alias)


@pytest.mark.parametrize("library_paths", [(), ("/operator-gpu/lib",)])
def test_bounded_runner_never_inherits_service_secrets(tmp_path, monkeypatch, library_paths):
    monkeypatch.setenv("SYNTHETIC_TEST_SECRET", "never-inherit")
    output = BoundedSubprocessRunner(library_paths=library_paths).run(
        [sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))"],
        input_payload=b"",
        max_stdout_bytes=4096,
        timeout_seconds=5,
        cwd=Path(tmp_path),
    )
    assert output.returncode == 0
    environment = json.loads(output.stdout)
    assert "SYNTHETIC_TEST_SECRET" not in environment
    assert environment.get("LD_LIBRARY_PATH") == (":".join(library_paths) if library_paths else None)
