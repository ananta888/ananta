"""Closed infrastructure inputs and cleanup of only the fixture-owned container."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_worker_container import DialogWorkerContainer

pytestmark = pytest.mark.timeout(45)
NETWORK = "meet-test-tls-11111111-1111-1111-1111-111111111111-network"
IMAGE = "sha256:" + "a" * 64
HUB = "http://172.30.0.1:12345/api/meet/v1/internal/dialog"
PIN = "a" * 43 + "="


@pytest.mark.parametrize(
    "patch",
    [
        {"network": "serving-network"},
        {"image": "mutable:latest"},
        {"hub_url": "https://public.example.test/api/meet/v1/internal/dialog"},
        {"hub_url": HUB + "?scope=all"},
        {"lifetime": 0},
        {"lifetime": True},
        {"lifetime": 601},
        {"diagnostics": 1},
        {"diagnostics": "true"},
        {"control_diagnostics": 1},
        {"control_diagnostics": "true"},
        {"browser_documents": 1},
        {"browser_documents": "true"},
    ],
)
def test_bad_configuration_has_no_docker_side_effects(patch):
    command = Mock()
    with pytest.raises(ValueError):
        DialogWorkerContainer(**({"network": NETWORK, "image": IMAGE, "hub_url": HUB} | patch), command=command)
    command.assert_not_called()


def fixture(
    tmp_path,
    monkeypatch,
    failure=None,
    health=None,
    diagnostics=False,
    browser_documents=False,
    gpu=False,
    control_diagnostics=False,
):
    calls = []
    key, cert = tmp_path / "key", tmp_path / "cert"
    key.write_bytes(b"synthetic")
    cert.write_bytes(b"synthetic")

    def command(*args):
        calls.append(args)
        if args[0] == failure:
            raise TimeoutError("synthetic-command-timeout")
        if args[:2] == ("network", "inspect"):
            return json.dumps(
                [{"Internal": True, "IPAM": {"Config": [{"Gateway": "172.30.0.1", "Subnet": "172.30.0.0/24"}]}}]
            )
        if args[:2] == ("image", "inspect"):
            return IMAGE
        if args[-1] == "{{json .State}}":
            return json.dumps(health if health is not None else {"Running": True, "Health": {"Status": "healthy"}})
        if args[0] == "inspect":
            return "172.30.0.2"
        return ""

    worker = DialogWorkerContainer(
        NETWORK,
        IMAGE,
        HUB,
        diagnostics=diagnostics,
        browser_documents=browser_documents,
        gpu=gpu,
        command=command,
        control_diagnostics=control_diagnostics,
    )
    return SimpleNamespace(**locals())


def test_gpu_profile_adds_only_explicit_resource_port_and_memory_budget(tmp_path, monkeypatch):
    resources = Mock(return_value=["--env=LD_LIBRARY_PATH=/host-nvidia"])
    monkeypatch.setattr("tests.meet_dialog_gpu_resources.receive_gpu_arguments", resources)
    f = fixture(tmp_path, monkeypatch, gpu=True)
    try:
        f.worker.start(f.key, f.cert, PIN)
        create = next(call for call in f.calls if call[0] == "create")
        assert "--memory=4g" in create and "--memory=1g" not in create
        assert "--env=LD_LIBRARY_PATH=/host-nvidia" in create
        resources.assert_called_once_with(f.worker.command)
    finally:
        f.worker.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_private_timing_observation_mounts_only_two_fixed_readonly_test_adapters(tmp_path, monkeypatch, enabled):
    f = fixture(tmp_path, monkeypatch, control_diagnostics=enabled)
    f.worker.start(f.key, f.cert, PIN)
    create = next(call for call in f.calls if call[0] == "create")
    assert ("--env=MEET_TEST_CONTROL_DIAGNOSTICS=" + ("1" if enabled else "0")) in create
    extra = [arg for arg in create if "observer.py" in arg]
    assert len(extra) == 2 * int(enabled)
    assert all(arg.endswith(",readonly") and "dst=/test/meet_dialog_" in arg for arg in extra)
    assert not any("dst=/app" in arg for arg in create)
    f.worker.close()


@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("browser_documents", [False, True])
def test_full_worker_has_no_source_or_hub_mounts_and_cleanup_is_idempotent(
    tmp_path, monkeypatch, diagnostics, browser_documents
):
    f = fixture(tmp_path, monkeypatch, diagnostics=diagnostics, browser_documents=browser_documents)
    f.worker.start(f.key, f.cert, PIN)
    create = next(call for call in f.calls if call[0] == "create")
    assert all(
        flag in create
        for flag in [
            "--read-only",
            "--cap-drop=ALL",
            "--user=1000:1000",
            "--cpus=2",
            "--memory=1g",
            "--pids-limit=256",
            "--env=MEET_DIALOG_ENABLED=1",
            "--env=MEET_DIALOG_DIAGNOSTICS_ENABLED=" + ("1" if diagnostics else "0"),
            "--env=MEET_TEST_PUBLIC_DOCUMENT=" + ("1" if browser_documents else "0"),
        ]
    )
    assert not any("docker.sock" in part or "dst=/app" in part for part in create)
    assert f.worker.origin == "http://172.30.0.2:8094"
    f.worker.close()
    f.worker.close()
    assert [call for call in f.calls if call[0] == "rm"] == [("rm", "--force", f.worker.name)]


@pytest.mark.parametrize("failure", ["create", "start", "inspect"])
def test_uncertain_create_or_partial_start_still_removes_only_owned_target(tmp_path, monkeypatch, failure):
    f = fixture(tmp_path, monkeypatch, failure)
    with pytest.raises(TimeoutError):
        try:
            f.worker.start(f.key, f.cert, PIN)
        finally:
            f.worker.close()
    assert [call for call in f.calls if call[0] == "rm"] == [("rm", "--force", f.worker.name)]


def test_missing_mount_or_bad_pin_cannot_create_container(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="test_worker_start_invalid"):
        f.worker.start(f.key, f.cert, "not-a-pin")
    assert f.calls == []
    with pytest.raises(ValueError, match="test_worker_mount_invalid"):
        f.worker.start(f.key, tmp_path / "missing", PIN)
    assert not any(call[0] == "create" for call in f.calls)


@pytest.mark.parametrize("health", [{"Running": False}, {"Running": True, "Health": {"Status": "unhealthy"}}])
def test_failed_health_is_bounded_and_cleans_owned_container(tmp_path, monkeypatch, health):
    f = fixture(tmp_path, monkeypatch, health=health)
    ticks = iter([0, 1, 21])
    monkeypatch.setattr(
        "tests.meet_dialog_worker_container.time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _seconds: None),
    )
    reason = "test_worker_stopped_before_health" if not health["Running"] else "test_worker_start_timeout"
    with pytest.raises(ValueError, match=reason):
        try:
            f.worker.start(f.key, f.cert, PIN)
        finally:
            f.worker.close()
    assert [call for call in f.calls if call[0] == "rm"] == [("rm", "--force", f.worker.name)]


def test_started_fixture_cannot_create_another_container(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    try:
        f.worker.start(f.key, f.cert, PIN)
        calls = list(f.calls)
        with pytest.raises(ValueError, match="test_worker_start_invalid"):
            f.worker.start(f.key, f.cert, PIN)
        assert f.calls == calls
    finally:
        f.worker.close()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        "[]",
        "{}",
        "x" * 257,
        '{"open":1,"control_revision":1,"receive_revision":1}',
        '{"open":true,"control_revision":true,"receive_revision":1}',
        '{"open":true,"control_revision":1,"receive_revision":-1}',
        '{"open":true,"control_revision":1,"receive_revision":1,"extra":1}',
    ],
)
def test_partial_or_malformed_runtime_marker_never_proves_chat_readiness(raw):
    worker = DialogWorkerContainer(NETWORK, IMAGE, HUB, command=Mock(return_value=raw))
    assert worker.chat_state() is None


def test_closed_runtime_marker_contains_only_open_state_and_revisions():
    expected = {"open": True, "control_revision": 2, "receive_revision": 7}
    command = Mock(return_value=json.dumps(expected))
    worker = DialogWorkerContainer(NETWORK, IMAGE, HUB, command=command)
    assert worker.chat_state() == expected
    assert command.call_args.args[:5] == ("exec", worker.name, "python", "-S", "-c")


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": 0},
        {"mode": "raw"},
        {"loaded": 1},
        {"failed": "no"},
        {"url": "https://example.com"},
    ],
)
def test_browser_marker_rejects_unbounded_or_authority_shaped_fields(changes):
    value = {"revision": 2, "mode": "off", "loaded": True, "failed": False} | changes
    worker = DialogWorkerContainer(NETWORK, IMAGE, HUB, command=Mock(return_value=json.dumps(value)))
    assert worker.browser_state() is None


def test_browser_marker_exposes_only_closed_test_observations():
    value = {"revision": 3, "mode": "browser", "loaded": True, "failed": False}
    command = Mock(return_value=json.dumps(value))
    worker = DialogWorkerContainer(NETWORK, IMAGE, HUB, command=command)
    assert worker.browser_state() == value
    assert command.call_args.args[:5] == ("exec", worker.name, "python", "-S", "-c")
