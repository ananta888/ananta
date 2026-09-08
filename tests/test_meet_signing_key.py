"""Actual private files and deterministic FD races; no operator keys or prompts."""

import errno
import os
import socket
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from agent.services.meet_signing_key import MAX_KEY_BYTES, load_meet_signing_key


@pytest.fixture
def private(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path = tmp_path / "synthetic-private.pem"
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, key, raw


@pytest.mark.parametrize("mode", [0o600, 0o400])
@pytest.mark.parametrize("symlink", [False, True])
def test_regular_private_secret_and_symlink_mount_sign_with_exact_key(private, tmp_path, mode, symlink):
    path, key, _ = private
    path.chmod(mode)
    if symlink:
        mounted = tmp_path / "synthetic-mounted-secret"
        mounted.symlink_to(path)
        path = mounted
    loaded = load_meet_signing_key(path)
    message = b"synthetic grant signing challenge"
    key.public_key().verify(loaded.sign(message), message)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_group_or_other_access_is_denied_before_reading(private, monkeypatch, mode):
    path, _, _ = private
    path.chmod(mode)
    read = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ValueError, match="^meet_machine_key_permissions$"):
        load_meet_signing_key(path)
    read.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "socket", "empty", "oversize"])
def test_invalid_file_type_size_or_missing_path_is_bounded(tmp_path, kind):
    path = tmp_path / "synthetic-invalid-key"
    connection = None
    try:
        if kind == "directory":
            path.mkdir(mode=0o700)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "socket":
            connection = socket.socket(socket.AF_UNIX)
            connection.bind(str(path))
            path.chmod(0o600)
        elif kind in {"empty", "oversize"}:
            path.write_bytes(b"x" * (MAX_KEY_BYTES + 1 if kind == "oversize" else 0))
            path.chmod(0o600)
        with pytest.raises(ValueError, match="^meet_machine_key_(unavailable|file_invalid)$"):
            load_meet_signing_key(path)
    finally:
        if connection is not None:
            connection.close()


@pytest.mark.parametrize("kind", ["invalid", "encrypted", "wrong_type"])
def test_key_parser_errors_are_redacted_and_never_request_passwords(private, kind):
    path, key, _ = private
    raw = b"SYNTHETIC_PRIVATE_PARSER_MARKER"
    expected = "meet_machine_key_invalid"
    if kind == "encrypted":
        raw = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"synthetic-password"),
        )
    elif kind == "wrong_type":
        raw = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        expected = "meet_machine_key_type_invalid"
    path.write_bytes(raw)
    with pytest.raises(ValueError) as error:
        load_meet_signing_key(path)
    assert str(error.value) == expected and "SYNTHETIC_PRIVATE" not in str(error.value)


@pytest.mark.parametrize("replacement", ["regular", "fifo", "permissions"])
def test_opened_descriptor_is_revalidated_after_path_swap(private, tmp_path, monkeypatch, replacement):
    path, _, raw = private
    alternate = tmp_path / "synthetic-replacement"
    if replacement == "fifo":
        os.mkfifo(alternate, 0o600)
    else:
        alternate.write_bytes(raw)
        alternate.chmod(0o644 if replacement == "permissions" else 0o600)
    original_open = os.open
    descriptors = []

    def swap(name, flags):
        assert flags & os.O_NONBLOCK and flags & os.O_CLOEXEC
        alternate.replace(path)
        descriptor = original_open(name, flags)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises(ValueError, match="^meet_machine_key_(changed|file_invalid|permissions)$"):
        load_meet_signing_key(path)
    assert len(descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("change", ["content", "permissions", "short_read", "read_error"])
def test_in_place_mutation_and_read_failure_close_the_exact_descriptor(private, monkeypatch, change):
    path, _, raw = private
    original_read = os.read
    descriptors = []

    def read(descriptor, maximum):
        assert maximum == MAX_KEY_BYTES + 1
        descriptors.append(descriptor)
        if change == "read_error":
            raise OSError("SYNTHETIC_PRIVATE_READ_MARKER")
        result = original_read(descriptor, maximum)
        if change == "content":
            path.write_bytes(raw + b"\n")
        elif change == "permissions":
            path.chmod(0o644)
        else:
            return result[:-1]
        return result

    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ValueError) as error:
        load_meet_signing_key(path)
    assert str(error.value) == (
        "meet_machine_key_unavailable" if change == "read_error" else "meet_machine_key_changed"
    )
    assert len(descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF


def test_grant_issuer_uses_a_narrow_injected_key_loading_port_and_rejects_bad_issuer_first(private):
    from agent.services.meet_machine_grant import MeetMachineGrantIssuer

    path, key, _ = private
    loader = Mock(return_value=key)
    assert MeetMachineGrantIssuer("https://synthetic-hub.test", path, key_loader=loader).key is key
    loader.assert_called_once_with(path)
    with pytest.raises(ValueError, match="issuer_invalid"):
        MeetMachineGrantIssuer("http://untrusted.test", path, key_loader=loader)
    loader.assert_called_once()


@pytest.mark.parametrize("path", [None, True, 3, "SYNTHETIC_PRIVATE\0PATH"])
def test_invalid_path_types_and_nul_paths_have_one_fixed_error(path):
    with pytest.raises(ValueError, match="^meet_machine_key_unavailable$"):
        load_meet_signing_key(path)
