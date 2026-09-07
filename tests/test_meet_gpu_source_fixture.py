"""Deterministic isolation and cleanup checks; no Docker or hardware required."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tests.meet_gpu_source_fixture import REQUIRED, driver_bindings, probe_command, run_probe

IMAGE = "sha256:" + "a" * 64
NAME = "meet-test-gpu-12345678-1234-1234-1234-123456789abc"


def mounts():
    return [
        {
            "Destination": "/host-nvidia/" + name,
            "Source": "/usr/lib/x86_64-linux-gnu/" + name,
            "Type": "bind",
            "RW": False,
        }
        for name in sorted(REQUIRED)
    ]


def test_only_exact_read_only_driver_projection_is_shared():
    private = {"Destination": "/run/secrets/key", "Source": "/private/key", "Type": "bind", "RW": False}
    assert driver_bindings(mounts() + [private]) == driver_bindings(mounts())


@pytest.mark.parametrize(
    "field,value",
    [
        ("RW", True),
        ("Type", "volume"),
        ("Source", "/private/libcuda.so"),
        ("Source", "/usr/lib/x86_64-linux-gnu/../secret"),
        ("Destination", "/host-nvidia/../../private"),
    ],
)
def test_unsafe_driver_bindings_fail_before_container_creation(field, value):
    values = mounts()
    values[0][field] = value
    with pytest.raises(ValueError):
        driver_bindings(values)


def test_missing_and_duplicate_driver_bindings_fail_closed():
    for values in (mounts()[:-1], mounts() + mounts()):
        with pytest.raises(ValueError):
            driver_bindings(values)


def test_current_source_probe_has_no_service_state_network_or_docker_socket(tmp_path):
    args = probe_command(NAME, IMAGE, driver_bindings(mounts()), "persona_visual_smoke", tmp_path)
    for value in ("--network=none", "--pull=never", "--read-only", "--user=1000:1000", "--cap-drop=ALL"):
        assert value in args
    assert args[-4:] == ["45", "python", "-m", "worker.meet_media.persona_visual_smoke"]
    assert not any(value in " ".join(args) for value in ("docker.sock", "/state", "/run/secrets", "--privileged"))
    for index, arg in enumerate(args):
        if arg == "--mount":
            assert args[index + 1].endswith(",readonly")
    assert "--signal=TERM" in args and "--kill-after=5" in args


@pytest.mark.parametrize("failure", ["create", "start", "report", None])
def test_exact_owned_container_is_removed_even_after_uncertain_execution(failure):
    report = {"status": "passed", "production_release_evidence": False, "human_capture_used": False}

    def reply(*args):
        if args[0] == "inspect":
            return IMAGE if args[-1] == "{{.Image}}" else json.dumps(mounts())
        if args[0] == failure:
            raise subprocess.TimeoutExpired(args[0], 55)
        if args[0] == "start":
            return "invalid-json" if failure == "report" else json.dumps(report)
        return ""

    command = Mock(side_effect=reply)
    if failure:
        with pytest.raises((subprocess.TimeoutExpired, ValueError)):
            run_probe("speech_smoke", command=command)
    else:
        assert run_probe("speech_smoke", command=command) == report
    created = next(call.args for call in command.call_args_list if call.args[0] == "create")
    assert command.call_args.args == ("rm", "--force", created[2])


def test_unlisted_probe_never_invokes_docker():
    command = Mock()
    with pytest.raises(ValueError):
        run_probe("server", command=command)
    command.assert_not_called()
