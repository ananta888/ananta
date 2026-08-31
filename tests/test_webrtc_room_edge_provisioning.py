from __future__ import annotations

import ssl
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.provision_webrtc_room_edge import WebrtcEdgeConfig, WebrtcEdgeProvisionError, provision

CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBhTCCASugAwIBAgIUQW5hbnRhVGVzdENlcnRpZmljYXRlMDAwCgYIKoZIzj0EAwIw
EjEQMA4GA1UEAwwHdGVzdC5sb2NhbDAeFw0yNjAxMDEwMDAwMDBaFw0yNzAxMDEwMDAw
MDBaMBIxEDAOBgNVBAMMB3Rlc3QubG9jYWwwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAR5vJxJ1rE5C8xKzmrV1OWF6O2x4gJz4gXWwLa8xFv8V5R9xwGvY2f7gqJb0qvU
f1JzjJtWc0yY1+fC5zZ2o1MwUTAdBgNVHQ4EFgQUdGVzdC1hbmFudGEtY2VydC0wMDAw
HwYDVR0jBBgwFoAUdGVzdC1hbmFudGEtY2VydC0wMDAwDwYDVR0TAQH/BAUwAwEB/zAK
BggqhkjOPQQDAgNIADBFAiEAuYxE1gH4j8rT8b8G5Z9mKf7n1jJtXzQwqVbE5pQxJfIC
IC7Jq2n2Jm4h3qYtJ0D0d3o6M4V2k9p5v8b2Y3u4q1r2
-----END CERTIFICATE-----
"""


def _config(tmp_path: Path) -> WebrtcEdgeConfig:
    certificate = tmp_path / "certificate.pem"
    private_key = tmp_path / "private-key.pem"
    certificate.write_text(CERTIFICATE)
    private_key.write_text("test-key")
    private_key.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return WebrtcEdgeConfig("webrtc-edge", "webrtc-room-server", certificate, private_key)


def test_preflight_fails_closed_for_missing_or_unsafe_secrets(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("ssl._ssl._test_decode_cert", lambda _path: {})
    monkeypatch.setattr("ssl.SSLContext.load_cert_chain", lambda *_args: None)
    config.private_key.chmod(0o644)
    with pytest.raises(WebrtcEdgeProvisionError, match="private_key_permissions_unsafe"):
        config.validate()


def test_dry_run_reports_idempotent_network_actions(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("ssl._ssl._test_decode_cert", lambda _path: {})
    monkeypatch.setattr("ssl.SSLContext.load_cert_chain", lambda *_args: None)
    calls: list[tuple[str, ...]] = []

    def runner(command):
        command = tuple(command)
        calls.append(command)
        if command[:3] == ("docker", "network", "inspect"):
            return subprocess.CompletedProcess(command, 1, "", "missing")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    commands = provision(config, apply=False, runner=runner)
    assert ("docker", "network", "create", "webrtc-edge") in commands
    assert ("docker", "network", "connect", "webrtc-edge", "webrtc-room-server") in commands
    assert all(command[2] not in {"create", "connect"} for command in calls)


def test_preflight_rejects_certificate_key_mismatch(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("ssl._ssl._test_decode_cert", lambda _path: {})

    def reject_mismatch(*_args):
        raise ssl.SSLError("key values mismatch")

    monkeypatch.setattr("ssl.SSLContext.load_cert_chain", reject_mismatch)
    with pytest.raises(WebrtcEdgeProvisionError, match="certificate_key_invalid"):
        config.validate()
