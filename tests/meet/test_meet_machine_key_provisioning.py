"""Explicit key-only setup, confined to temporary files and bounded subprocesses."""

import fcntl
import json
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.meet_machine_key_provisioning import PRIVATE, PUBLIC, provision_meet_machine_keys
from agent.services.meet_signing_key import load_meet_signing_key


@pytest.mark.timeout(15)
@pytest.mark.parametrize("fifo", ["machine-private.pem", "machine-public.pem"])
def test_existing_key_pair_fifo_is_rejected_without_waiting_for_a_writer(tmp_path, fifo):
    for name in ("machine-private.pem", "machine-public.pem"):
        path = tmp_path / name
        if name == fifo:
            os.mkfifo(path, 0o600)
        else:
            path.write_bytes(
                Ed25519PrivateKey.generate().private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
                if name == "machine-private.pem"
                else b"synthetic public marker"
            )
            path.chmod(0o600)
    script = """
import sys
from pathlib import Path
from scripts.setup_meet_media import provision_machine_keys
try:
    provision_machine_keys(Path(sys.argv[1]))
except ValueError:
    pass
else:
    raise AssertionError('FIFO admitted as a machine key')
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("machine_key_provisioning_waited_for_a_fifo_writer", pytrace=False)
    assert result.returncode == 0, "bounded machine-key file rejection missing"


def test_new_pair_signs_and_repeat_invocation_preserves_exact_inodes_and_bytes(tmp_path):
    result = provision_meet_machine_keys(tmp_path)
    before = {name: ((tmp_path / name).stat().st_ino, (tmp_path / name).read_bytes()) for name in (PRIVATE, PUBLIC)}
    assert provision_meet_machine_keys(tmp_path) == result
    assert {name: ((tmp_path / name).stat().st_ino, (tmp_path / name).read_bytes()) for name in before} == before
    key = load_meet_signing_key(tmp_path / PRIVATE)
    public = serialization.load_pem_public_key((tmp_path / PUBLIC).read_bytes())
    public.verify(key.sign(b"synthetic machine challenge"), b"synthetic machine challenge")
    assert len(result["public_key_sha256"]) == 64 and result["status"] == "ready"
    assert result["trust_activated"] is result["production_release_evidence"] is False
    assert set(p.name for p in tmp_path.iterdir()) == {PRIVATE, PUBLIC}
    assert all((tmp_path / name).stat().st_mode & 0o077 == 0 for name in before)
    assert "PRIVATE" not in str(result) and str(tmp_path) not in str(result)


def test_private_only_interrupted_pair_finishes_without_changing_identity(tmp_path):
    original = provision_meet_machine_keys(tmp_path)
    before = (tmp_path / PRIVATE).read_bytes()
    (tmp_path / PUBLIC).unlink()  # Only the test-owned generated public half.
    assert provision_meet_machine_keys(tmp_path) == original
    assert (tmp_path / PRIVATE).read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        "public_only",
        "mismatch",
        "private_permissions",
        "public_writable",
        "private_large",
        "public_large",
        "private_symlink",
        "public_symlink",
    ],
)
def test_existing_invalid_pair_is_never_overwritten_or_repaired_to_another_identity(tmp_path, change):
    provision_meet_machine_keys(tmp_path)
    if change == "public_only":
        (tmp_path / PRIVATE).unlink()
    elif change == "mismatch":
        (tmp_path / PUBLIC).write_bytes(b"synthetic mismatched public key")
    elif change.endswith("permissions"):
        (tmp_path / PRIVATE).chmod(0o640)
    elif change == "public_writable":
        (tmp_path / PUBLIC).chmod(0o666)
    else:
        name = PRIVATE if change.startswith("private") else PUBLIC
        path = tmp_path / name
        if change.endswith("large"):
            path.write_bytes(b"x" * 4097)
        else:
            target = tmp_path / "synthetic-mounted-key"
            path.rename(target)
            path.symlink_to(target)
    before = {p.name: (p.lstat().st_mode, p.read_bytes()) for p in tmp_path.iterdir()}
    with pytest.raises(ValueError):
        provision_meet_machine_keys(tmp_path)
    assert {p.name: (p.lstat().st_mode, p.read_bytes()) for p in tmp_path.iterdir()} == before


def test_existing_readonly_private_and_public_readable_key_pair_remain_compatible(tmp_path):
    result = provision_meet_machine_keys(tmp_path)
    (tmp_path / PRIVATE).chmod(0o400)
    (tmp_path / PUBLIC).chmod(0o644)
    assert provision_meet_machine_keys(tmp_path) == result


@pytest.mark.parametrize("kind", ["relative", "root", "symlink", "permissions"])
def test_provisioning_requires_an_explicit_private_nonsymlink_directory(tmp_path, kind):
    path = tmp_path
    if kind == "relative":
        path = "relative-synthetic-directory"
    elif kind == "root":
        path = "/"
    elif kind == "permissions":
        tmp_path.chmod(0o755)
    else:
        path = tmp_path / "synthetic-link"
        path.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="directory_invalid"):
        provision_meet_machine_keys(path)
    assert not (tmp_path / PRIVATE).exists()


def test_competing_provisioner_gets_bounded_busy_without_creating_keys(tmp_path):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="^meet_machine_key_provisioning_busy$"):
            provision_meet_machine_keys(tmp_path)
        assert list(tmp_path.iterdir()) == []
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("failure", ["write", "short_write", "fsync", "link"])
def test_failed_first_install_leaves_no_partial_key_or_temporary_file(tmp_path, monkeypatch, failure):
    original = os.open
    descriptors = []

    def tracked_open(*args, **kwargs):
        value = original(*args, **kwargs)
        descriptors.append(value)
        return value

    monkeypatch.setattr(os, "open", tracked_open)
    if failure == "short_write":
        monkeypatch.setattr(os, "write", Mock(return_value=1))
    else:
        monkeypatch.setattr(os, failure, Mock(side_effect=OSError("synthetic private details")))
    with pytest.raises(ValueError) as result:
        provision_meet_machine_keys(tmp_path)
    assert "synthetic private details" not in str(result.value)
    assert list(tmp_path.iterdir()) == []
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_public_install_failure_leaves_valid_private_key_for_explicit_resume(tmp_path, monkeypatch):
    import agent.services.meet_machine_key_provisioning as provisioning

    install = provisioning.install_key_file

    def interrupted(directory_fd, name, content):
        if name == PUBLIC:
            raise OSError("synthetic public install failure")
        return install(directory_fd, name, content)

    with monkeypatch.context() as context:
        context.setattr(provisioning, "install_key_file", interrupted)
        with pytest.raises(ValueError, match="unavailable"):
            provision_meet_machine_keys(tmp_path)
    original = (tmp_path / PRIVATE).read_bytes()
    assert not (tmp_path / PUBLIC).exists()
    assert provision_meet_machine_keys(tmp_path)["status"] == "ready"
    assert (tmp_path / PRIVATE).read_bytes() == original


def test_exclusive_publish_never_overwrites_a_concurrent_target(tmp_path):
    from agent.services.meet_machine_key_files import install_key_file

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    (tmp_path / PRIVATE).write_bytes(b"synthetic preexisting target")
    try:
        with pytest.raises(FileExistsError):
            install_key_file(descriptor, PRIVATE, b"synthetic replacement")
        assert (tmp_path / PRIVATE).read_bytes() == b"synthetic preexisting target"
        assert {p.name for p in tmp_path.iterdir()} == {PRIVATE}
    finally:
        os.close(descriptor)


def test_replaced_directory_descriptor_is_rejected_and_closed_before_install(tmp_path, monkeypatch):
    source, alternate = tmp_path / "source", tmp_path / "alternate"
    source.mkdir(mode=0o700)
    alternate.mkdir(mode=0o700)
    original_open = os.open
    descriptors = []

    def changed_open(path, flags, *args, **kwargs):
        descriptor = original_open(alternate if path == source else path, flags, *args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", changed_open)
    with pytest.raises(ValueError, match="^meet_machine_key_directory_changed$"):
        provision_meet_machine_keys(source)
    assert list(source.iterdir()) == list(alternate.iterdir()) == []
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.timeout(15)
@pytest.mark.parametrize("blocked", [False, True])
def test_actual_key_only_cli_is_headless_redacted_and_never_provisions_models(tmp_path, blocked):
    target = tmp_path / "owned-key-only"
    target.mkdir(mode=0o700)
    if blocked:
        os.mkfifo(target / PRIVATE, 0o600)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.provision_meet_machine_keys", str(target)],
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == (2 if blocked else 0) and result.stderr == ""
    value = json.loads(result.stdout)
    assert value["status"] == ("blocked" if blocked else "ready")
    assert value["trust_activated"] is False
    assert str(target) not in result.stdout and "PRIVATE" not in result.stdout
    assert {p.name for p in target.iterdir()} == ({PRIVATE} if blocked else {PRIVATE, PUBLIC})
