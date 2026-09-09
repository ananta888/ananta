"""Negative and resource-bound tests for the synthetic browser launch adapter."""

import json
import signal
import subprocess
from unittest.mock import Mock

import pytest

from tests.meet_dialog_browser_fixture import DialogBrowserFixture
from tests.test_meet_dialog_cross_repository import close_bridge, process_usage

NETWORK = "meet-test-tls-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-network"


def fixture(*, internal=True, launch_failure=False):
    def command(*args):
        if args[:2] == ("network", "inspect"):
            return json.dumps([{"Internal": internal}])
        if args[0] == "image":
            return "sha256:" + "a" * 64
        if args[0] == "inspect":
            return "1234" if args[-1] == "{{.State.Pid}}" else "172.30.0.3"
        if args[0] == "logs":
            return "test_browser_launch_failed" if launch_failure else "ws://localhost:8099/" + "b" * 32
        return ""

    run = Mock(side_effect=command)
    return DialogBrowserFixture(NETWORK, 180, command=run), run


def test_browser_uses_owned_internal_network_and_existing_sandbox_without_host_exposure():
    browser, run = fixture()
    browser.start("a" * 43 + "=")
    try:
        create = next(c.args for c in run.call_args_list if c.args[0] == "create")
        assert "--user=1000:1000" in create and "--read-only" in create
        assert "--cap-drop=ALL" in create and "--security-opt=no-new-privileges" in create
        assert "--network" in create and create[create.index("--network") + 1] == NETWORK
        assert any(arg.endswith("/docker/meet-media/chromium-seccomp.json") for arg in create)
        assert not any(
            arg in {"-p", "-v", "--privileged"}
            or arg.startswith(("--publish", "--mount", "--volume", "--network=host", "--cap-add"))
            for arg in create
        )
        assert "chromiumSandbox:true" in create[-1] and "--no-sandbox" not in create[-1]
        assert browser.process_id == 1234
        target = Mock()
        result = browser.launch(
            target, headless=True, chromium_sandbox=True, args=["--autoplay-policy=no-user-gesture-required"]
        )
        assert result is target.connect.return_value
        target.connect.assert_called_once_with("ws://172.30.0.3:8099/" + "b" * 32, timeout=15000)
        with pytest.raises(ValueError, match="launch_contract_changed"):
            browser.launch(target, headless=True, chromium_sandbox=False)
        with pytest.raises(ValueError, match="start_invalid"):
            browser.start("a" * 43 + "=")
    finally:
        browser.close()
    browser.close()
    removed = [c.args for c in run.call_args_list if c.args[0] == "rm"]
    assert removed == [("rm", "--force", browser.name)]


def test_failed_browser_removal_does_not_discard_owned_cleanup_capability():
    browser, run = fixture()
    browser.start("a" * 43 + "=")
    run.side_effect = RuntimeError("synthetic removal failed")
    with pytest.raises(RuntimeError, match="removal failed"):
        browser.close()
    assert browser.created
    run.side_effect = None
    browser.close()
    assert not browser.created


@pytest.mark.parametrize("network", ["bridge", "host", "meet-test-tls-../../other-network", None])
def test_browser_rejects_non_fixture_network_before_commands(network):
    run = Mock()
    with pytest.raises(ValueError, match="network_invalid"):
        DialogBrowserFixture(network, 180, command=run)
    run.assert_not_called()


def test_browser_rejects_external_network_without_allocating():
    browser, run = fixture(internal=False)
    with pytest.raises(ValueError, match="network_invalid"):
        browser.start("a" * 43 + "=")
    browser.close()
    assert run.call_count == 1


def test_failed_sandbox_is_bounded_and_cleanup_does_not_remove_meet_network():
    browser, run = fixture(launch_failure=True)
    try:
        with pytest.raises(ValueError, match="sandbox_launch_failed"):
            browser.start("a" * 43 + "=")
    finally:
        browser.close()
    assert not any(c.args[:2] == ("network", "rm") for c in run.call_args_list)
    assert run.call_args.args == ("rm", "--force", browser.name)


@pytest.mark.parametrize("lifetime", [179, 7381, True, "180"])
def test_browser_lifetime_requires_an_explicit_bounded_integer(lifetime):
    with pytest.raises(ValueError, match="lifetime_invalid"):
        DialogBrowserFixture(NETWORK, lifetime)


def test_node_trust_mount_is_only_the_exact_already_pinned_private_certificate(tmp_path):
    from tests.meet_browser_network_server import certificate

    pem, _, spki = certificate(tmp_path)
    browser, run = fixture()
    browser.start(spki, certificate=pem)
    create = next(c.args for c in run.call_args_list if c.args[0] == "create")
    assert create[create.index("--mount") + 1] == f"type=bind,src={pem},dst=/test/meet-ca.pem,readonly"
    assert "--env=NODE_EXTRA_CA_CERTS=/test/meet-ca.pem" in create
    assert create.count("--mount") == 1
    browser.close()


@pytest.mark.parametrize("failure", ["mismatch", "symlink", "missing", "bundle", "malformed"])
def test_invalid_private_ca_cannot_create_a_browser_or_broaden_trust(tmp_path, failure):
    from tests.meet_browser_network_server import certificate

    pem, _, spki = certificate(tmp_path)
    if failure == "mismatch":
        spki = "a" * 43 + "="
    elif failure == "symlink":
        linked = tmp_path / "linked.pem"
        linked.symlink_to(pem)
        pem = linked
    elif failure == "missing":
        pem = tmp_path / "missing.pem"
    else:
        pem.write_bytes(pem.read_bytes() * 2 if failure == "bundle" else b"invalid")
    browser, run = fixture()
    with pytest.raises(ValueError, match="certificate_invalid"):
        browser.start(spki, certificate=pem)
    run.assert_not_called()


def test_bridge_cleanup_allows_bounded_docker_teardown_then_reaps_only_owned_group(monkeypatch):
    bridge = Mock(pid=1234)
    bridge.poll.return_value = None
    bridge.stdin.write.side_effect = BrokenPipeError()
    bridge.wait.side_effect = [subprocess.TimeoutExpired("synthetic_bridge", 100), 0]
    kill = Mock()
    monkeypatch.setattr("os.killpg", kill)
    close_bridge(bridge)
    kill.assert_called_once_with(1234, signal.SIGKILL)
    assert [c.kwargs for c in bridge.wait.call_args_list] == [{"timeout": 100}, {"timeout": 5}]
    bridge.stdin.close.assert_called_once()
    bridge.stdout.close.assert_called_once()


def test_bridge_already_exited_still_closes_both_pipes():
    bridge = Mock()
    bridge.poll.return_value = 1
    close_bridge(bridge)
    bridge.wait.assert_not_called()
    bridge.stdin.close.assert_called_once()
    bridge.stdout.close.assert_called_once()


@pytest.mark.parametrize("broken_close", [False, True])
def test_bridge_sends_eof_before_waiting_for_node_to_exit(broken_close):
    bridge = Mock()
    bridge.poll.return_value = None
    if broken_close:
        bridge.stdin.close.side_effect = BrokenPipeError()

    def wait(**kwargs):
        bridge.stdin.close.assert_called_once()
        return 0

    bridge.wait.side_effect = wait
    close_bridge(bridge)
    bridge.stdout.close.assert_called_once()


def test_process_measurement_deduplicates_host_and_container_trees_and_handles_exit():
    import psutil

    root, child, exited = Mock(pid=1), Mock(pid=2), Mock(pid=3)
    root.children.return_value = [child, exited]
    child.children.return_value = []
    root.memory_info.return_value.rss = 100
    child.memory_info.return_value.rss = 200
    exited.memory_info.side_effect = psutil.NoSuchProcess(3)
    assert process_usage(root, child) == (300, 3)
    child.memory_info.assert_called_once()
