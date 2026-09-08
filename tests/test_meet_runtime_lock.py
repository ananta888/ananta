"""Closed build inventory and bounded diagnostics, not GPU readiness evidence."""

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from scripts import check_meet_runtime_lock as lock

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "docker/meet-media/requirements.lock"
SCRIPT_PATH = ROOT / "scripts/check_meet_runtime_lock.py"
pytestmark = pytest.mark.timeout(10)


def baseline():
    return lock.parse_lock(LOCK_PATH.read_bytes())


def test_pinned_inventory_matches_all_direct_media_and_browser_requirements():
    expected = baseline()
    for requirement in (ROOT / "docker/meet-media/requirements.txt").read_text().splitlines():
        name, version = requirement.split("==")
        name = re.sub(r"\[.*\]", "", name).lower()
        assert expected[name] == version
    assert expected["playwright"] == "1.58.0"
    assert expected["onnxruntime-gpu"] == "1.22.0"
    assert len(expected) == 43
    runtime = {name: version for name, version in expected.items() if name != "onnxruntime"}
    assert lock.runtime_matches(expected, runtime)
    assert not lock.runtime_matches(expected, expected)  # CPU distribution must not survive.


@pytest.mark.parametrize(
    "extra",
    [
        b"numpy>=2\n",
        b"numpy\n",
        b"numpy==2.2.6\n",
        b"NumPy==2.2.6\n",
        b"typing_extensions==4.16.0\n",
        b"other==1.2; python_version>'3'\n",
        b"other==1.2 --hash=sha256:anything\n",
        b"other @ https://private.invalid/secret\n",
        b"other==*\n",
        b"other==1.2==3\n",
        b"other==1.2 # inline comments denied\n",
        b"other==\xff\n",
    ],
)
def test_malformed_unpinned_or_duplicate_entries_are_rejected(extra):
    with pytest.raises(ValueError):
        lock.parse_lock(LOCK_PATH.read_bytes() + extra)


@pytest.mark.parametrize("raw", [None, "not-bytes", b"", b"# empty inventory\n", b"x" * 16385])
def test_missing_or_oversize_inventory_is_not_a_valid_lock(raw):
    with pytest.raises(ValueError):
        lock.parse_lock(raw)


@pytest.mark.parametrize("change", ["missing", "extra", "version", "cpu"])
def test_runtime_drift_fails_instead_of_repairing_or_ignoring_it(change):
    expected = baseline()
    actual = {name: version for name, version in expected.items() if name != "onnxruntime"}
    if change == "missing":
        del actual["numpy"]
    elif change == "extra":
        actual["unplanned-provider"] = "1.2"
    elif change == "cpu":
        actual["onnxruntime"] = expected["onnxruntime"]
    else:
        actual["onnxruntime-gpu"] = "1.29.0"
    assert not lock.runtime_matches(expected, actual)


def test_installed_metadata_normalizes_names_but_rejects_duplicate_distributions():
    package = SimpleNamespace(metadata={"Name": "typing_extensions"}, version="4.16.0")
    assert lock.installed_inventory([package]) == {"typing-extensions": "4.16.0"}
    with pytest.raises(ValueError, match="meet_runtime_inventory_invalid"):
        lock.installed_inventory([package, package])
    for package in (
        SimpleNamespace(metadata={}, version="1.2"),
        SimpleNamespace(metadata={"Name": "pkg"}, version="secret-not-a-version"),
    ):
        with pytest.raises(ValueError):
            lock.installed_inventory([package])


def test_inventory_entry_limits_bound_both_lock_and_installed_metadata():
    extra = b"".join(f"extra{i}==1.2\n".encode() for i in range(257))
    with pytest.raises(ValueError, match="meet_runtime_lock_invalid"):
        lock.parse_lock(LOCK_PATH.read_bytes() + extra)
    packages = [SimpleNamespace(metadata={"Name": f"extra{i}"}, version="1.2") for i in range(257)]
    with pytest.raises(ValueError, match="meet_runtime_inventory_invalid"):
        lock.installed_inventory(packages)


def test_malformed_installed_metadata_cannot_escape_fixed_cli_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(lock, "distributions", lambda: [SimpleNamespace(metadata=None, version="1.2")])
    assert lock.main([str(LOCK_PATH)]) == 1
    assert capsys.readouterr() == ("meet-runtime-lock-failed\n", "")


def test_actual_file_read_and_cli_only_emit_fixed_diagnostics(tmp_path, monkeypatch, capsys):
    expected = lock.read_lock(LOCK_PATH)
    packages = [
        SimpleNamespace(metadata={"Name": name}, version=version)
        for name, version in expected.items()
        if name != "onnxruntime"
    ]
    monkeypatch.setattr(lock, "distributions", lambda: packages)
    assert lock.main([str(LOCK_PATH)]) == 0
    assert capsys.readouterr() == ("meet-runtime-lock-ok\n", "")
    assert lock.main([str(tmp_path / "private-missing-path")]) == 1
    assert capsys.readouterr() == ("meet-runtime-lock-failed\n", "")
    assert lock.main([]) == 1
    assert capsys.readouterr() == ("meet-runtime-lock-failed\n", "")


@pytest.mark.parametrize("kind", ["fifo", "directory", "empty", "large", "invalid"])
def test_real_cli_rejects_bad_files_without_waiting_or_disclosing_paths(tmp_path, kind):
    target = tmp_path / "private-input"
    if kind == "fifo":
        os.mkfifo(target, 0o600)
    elif kind == "directory":
        target.mkdir()
    else:
        target.write_bytes({"empty": b"", "large": b"x" * 16385, "invalid": b"private-invalid-material"}[kind])
    result = subprocess.run([sys.executable, str(SCRIPT_PATH), str(target)], capture_output=True, timeout=2)
    assert result.returncode == 1
    assert result.stdout == b"meet-runtime-lock-failed\n" and result.stderr == b""


def test_failed_read_closes_its_descriptor(monkeypatch):
    close = Mock()
    monkeypatch.setattr(lock.os, "open", lambda *args: 123)
    monkeypatch.setattr(lock.os, "fstat", Mock(side_effect=OSError("private-fstat-error")))
    monkeypatch.setattr(lock.os, "close", close)
    with pytest.raises(OSError):
        lock.read_lock("synthetic")
    close.assert_called_once_with(123)


def test_docker_build_constrains_both_installs_and_checks_final_inventory():
    dockerfile = (ROOT / "docker/meet-media/Dockerfile").read_text()
    assert "COPY docker/meet-media/requirements.lock /app/requirements.lock" in dockerfile
    assert "pip install --no-cache-dir -c requirements.lock -r requirements.txt" in dockerfile
    assert "pip install --no-cache-dir -c requirements.lock playwright==1.58.0" in dockerfile
    assert "pip uninstall -y onnxruntime" in dockerfile
    assert "-c requirements.lock --force-reinstall --no-deps onnxruntime-gpu==1.22.0" in dockerfile
    assert dockerfile.index("RUN python /app/check_meet_runtime_lock.py") > dockerfile.index("install --with-deps")


@pytest.mark.timeout(45)
@pytest.mark.skipif(os.environ.get("MEET_RUNTIME_LOCK_GATE") != "1", reason="explicit private runtime inventory gate")
def test_real_installed_immutable_worker_inventory_without_gpu_or_network():
    image = os.environ.get("MEET_RUNTIME_LOCK_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    identity = uuid4().hex
    name = "ananta-meet-inventory-" + identity[:12]

    def docker(*args, timeout=5):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

    try:
        result = docker(
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--label",
            "ananta.test-run=" + identity,
            "--network=none",
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=256m",
            "--cpus=0.5",
            "--pids-limit=32",
            "--mount",
            f"type=bind,src={SCRIPT_PATH},dst=/check.py,readonly",
            "--mount",
            f"type=bind,src={LOCK_PATH},dst=/lock.txt,readonly",
            "--entrypoint=python",
            image,
            "/check.py",
            "/lock.txt",
            timeout=25,
        )
        assert result.returncode == 0, "private runtime inventory failed; container details redacted"
        assert result.stdout == "meet-runtime-lock-ok\n" and result.stderr == ""
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-run"}}', name)
        if owner.returncode == 0 and owner.stdout.strip() == identity:
            assert docker("rm", "-f", name).returncode == 0
