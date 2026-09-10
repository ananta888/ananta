"""Explicit Pi opt-in and file-managed credentials; no ambient provider state."""

from pathlib import Path

import pytest

from worker.runtime.native_graph.pi_configuration import NativePiWorkerProfile, PiProfileCredentialFiles
from worker.runtime.workflow_adapter_worker_profile import load_workflow_adapter_worker_profile


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"enabled": True},
        {"unknown": True},
        {"runtime_root": "relative"},
        {"runtime_root": "/tmp/../elsewhere"},
        {"credential_files": {"bad profile": "/run/secrets/test"}},
        {"credential_files": {"profile": "relative"}},
        {"credential_files": {"profile": 42}},
        {"credential_files": {str(i): "/run/secrets/test" for i in range(9)}},
    ],
)
def test_pi_worker_configuration_rejects_ambiguous_or_unbound_settings(values):
    with pytest.raises(ValueError):
        NativePiWorkerProfile.model_validate(values)


def test_pi_worker_is_disabled_by_default():
    profile = NativePiWorkerProfile()
    assert profile.enabled is False and profile.credential_files == {}


def test_shipped_pi_profile_is_explicit_separate_opt_in(tmp_path):
    root = Path(__file__).resolve().parents[1]

    def deployed(name):
        path = tmp_path / name
        path.write_text((root / "config/workflow_runtime" / name).read_text())
        path.chmod(0o400)
        return load_workflow_adapter_worker_profile(str(path))

    profile = deployed("pi_worker_profile.v1.json")
    native = profile.worker_runtime.native_graph
    assert native.pi.enabled is True and native.allowed_task_types == ["pi_coding_agent"]
    legacy = deployed("native_worker_profile.v1.json")
    assert legacy.worker_runtime.native_graph.pi is None


def test_pi_credentials_only_come_from_the_exact_managed_profile_file(tmp_path, monkeypatch):
    path = tmp_path / "credential"
    path.write_text("synthetic-model-key")
    path.chmod(0o400)
    monkeypatch.setenv("OPENAI_API_KEY", "foreign-key")
    profile = NativePiWorkerProfile(enabled=True, credential_files={"selected-profile": str(path)})
    credentials = PiProfileCredentialFiles(profile)
    profile.credential_files.clear()
    assert credentials.resolve("selected-profile") == "synthetic-model-key"
    with pytest.raises(ValueError, match="pi_profile_credential_not_configured"):
        credentials.resolve("foreign-profile")


@pytest.mark.parametrize("kind", ["symlink", "missing", "writable", "directory", "empty", "multiline"])
def test_pi_unsafe_or_missing_credential_file_never_falls_back_to_environment(tmp_path, monkeypatch, kind):
    path = tmp_path / "credential"
    if kind == "symlink":
        target = tmp_path / "other"
        target.write_text("synthetic-key")
        target.chmod(0o400)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind != "missing":
        path.write_text("" if kind == "empty" else "a\nb" if kind == "multiline" else "synthetic-key")
        path.chmod(0o666 if kind == "writable" else 0o400)
    monkeypatch.setenv("OPENAI_API_KEY", "foreign-key")
    credentials = PiProfileCredentialFiles(NativePiWorkerProfile(credential_files={"selected": str(path)}))
    with pytest.raises(ValueError, match="pi_profile_credential_unavailable"):
        credentials.resolve("selected")
