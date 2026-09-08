"""Guard lifecycle without privileges; real firewall checks use owned containers."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from tests.test_meet_egress_dns_server import serving
from tests.test_meet_egress_dns_wire import policy
from tests.test_meet_egress_policy import configuration
from worker.meet_egress.configuration import read_policy
from worker.meet_egress.enforcer import install_filters
from worker.meet_egress.health import check_health
from worker.meet_egress.service import serve_guard


def files(tmp_path):
    config, ready = tmp_path / "policy", tmp_path / "ready"
    config.write_text(json.dumps(configuration()))
    ready.write_text(policy().digest)
    return config, ready


def test_file_admission_never_follows_symlinks_or_blocks_on_special_files(tmp_path):
    config, _ = files(tmp_path)
    assert read_policy(config) == policy()
    link = tmp_path / "link"
    link.symlink_to(config)
    with pytest.raises(OSError):
        read_policy(link)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        read_policy(fifo)
    with pytest.raises(ValueError):
        read_policy(tmp_path)


def test_filters_validate_both_families_before_applying_only_filter():
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    install_filters(policy(), run=run)
    assert len(calls) == 4
    assert ["--test" in args for args, _ in calls] == [True, True, False, False]
    assert [args[0] for args, _ in calls] == ["/usr/sbin/ip6tables-restore", "/usr/sbin/iptables-restore"] * 2
    for args, kwargs in calls:
        assert args[1:3] == ["--wait", "2"]
        assert kwargs["timeout"] == 4 and "shell" not in kwargs
        assert kwargs["input"].startswith(b"*filter\n") and b"*nat" not in kwargs["input"]
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize("failure_at", range(4))
@pytest.mark.parametrize("error", [None, OSError("private"), subprocess.TimeoutExpired("private", 4)])
def test_failed_validation_or_install_never_continues(failure_at, error):
    count = 0

    def run(*args, **kwargs):
        nonlocal count
        count += 1
        if count == failure_at + 1:
            if error:
                raise error
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    with pytest.raises(RuntimeError, match="^meet_egress_filter_install_failed$"):
        install_filters(policy(), run=run)
    assert count == failure_at + 1


def test_readiness_requires_same_configuration_and_live_fixed_resolver(tmp_path):
    config, ready = files(tmp_path)
    with serving() as server:
        assert check_health(config, ready, port=server.port)
        changed = configuration()
        changed["endpoints"][0]["port"] = 444
        config.write_text(json.dumps(changed))
        assert not check_health(config, ready, port=server.port)
        config.write_text(json.dumps(configuration()))
        ready.write_text("x" * 65)
        assert not check_health(config, ready, port=server.port)


def test_guard_orders_install_bind_readiness_and_removal(tmp_path):
    ready, events = tmp_path / "ready", []

    def install(value):
        assert value == policy() and not ready.exists()
        events.append("install")

    class Dns:
        def __init__(self, value):
            assert not ready.exists()
            events.append("bind")

        def serve(self, stopped):
            assert ready.read_text() == policy().digest
            assert ready.stat().st_mode & 0o777 == 0o600
            events.append("serve")

        def close(self):
            assert not ready.exists()
            events.append("close")

    phases = []
    serve_guard(policy(), lambda: True, install=install, responder=Dns, readiness=ready, observe=phases.append)
    assert events == ["install", "bind", "serve", "close"]
    assert phases == ["filter", "dns_bind", "readiness", "dns_serve"]


def test_failed_filter_install_cannot_bind_or_create_readiness(tmp_path):
    def fail(_):
        raise RuntimeError("test-install-failure")

    def forbidden(_):
        pytest.fail("resolver must not bind after failed filter install")

    ready = tmp_path / "ready"
    with pytest.raises(RuntimeError, match="test-install-failure"):
        serve_guard(policy(), lambda: False, install=fail, responder=forbidden, readiness=ready)
    assert not ready.exists()


def test_resolver_failure_removes_readiness_and_closes_listener(tmp_path):
    ready, closed = tmp_path / "ready", []

    class Dns:
        def __init__(self, _):
            pass

        def serve(self, _):
            assert ready.exists()
            raise OSError("test-resolver-failure")

        def close(self):
            closed.append(True)

    with pytest.raises(OSError, match="test-resolver-failure"):
        serve_guard(policy(), lambda: False, install=lambda _: None, responder=Dns, readiness=ready)
    assert not ready.exists() and closed == [True]


def test_preexisting_readiness_is_not_overwritten_or_trusted(tmp_path):
    _, ready = files(tmp_path)
    ready.write_text("old")
    closed = []

    class Dns:
        def __init__(self, _):
            pass

        def serve(self, _):
            pytest.fail("stale readiness must not serve")

        def close(self):
            closed.append(True)

    with pytest.raises(FileExistsError):
        serve_guard(policy(), lambda: False, install=lambda _: None, responder=Dns, readiness=ready)
    assert ready.read_text() == "old" and closed == [True]
