"""Reservation precedes execution; changed inputs and skips never become acceptance."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.meet_test_gate_profiles import PROFILE_NAMES
from scripts.run_meet_test_gate import execute, run


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
@pytest.mark.parametrize("mode", ["pass", "fail", "skip", "changed", "exception"])
def test_reserved_test_scope_is_completed_without_promoting_failed_or_changed_inputs(
    tmp_path, monkeypatch, mode, profile_name
):
    from scripts.meet_test_gate_profiles import select_profile

    profile = select_profile(profile_name)
    events = []
    source = {"revision": "a" * 40, "digest": "b" * 64}
    snapshots = [(dict(source), (Path("test.py"),)) for _ in range(4)]
    if mode == "changed":
        snapshots[2][0]["digest"] = "c" * 64
    monkeypatch.setattr("scripts.run_meet_test_gate.snapshot_repository", Mock(side_effect=snapshots))
    monkeypatch.setattr(
        "scripts.run_meet_test_gate.native_environment",
        lambda: {"MEET_TEST_PUBLIC_DIR": "/synthetic", **dict.fromkeys(profile.image_inputs, "sha256:" + "a" * 64)},
    )
    monkeypatch.setenv("MEET_DIALOG_SOAK_SECONDS", "7200")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=tests")
    monkeypatch.setattr("scripts.run_meet_test_gate.frontend_digest", lambda _: "d" * 64)
    reserved = SimpleNamespace(
        source_id="synthetic-source-not-evidence",
        run_id="synthetic-run-not-evidence",
        assignment={"synthetic": True},
        complete=Mock(return_value={"synthetic": True, "production_release_eligible": False}),
    )

    def reserve(**kwargs):
        events.append("reserve")
        assert kwargs["task_id"] == "MAP-30"
        assert kwargs["environment"]["companion"] == source
        assert kwargs["policy_paths"][0] == Path("AGENTS.md")
        assert kwargs["execution_profile"] == profile.projection()
        return reserved

    def worker(command, environment, log_path, *, root, timeout):
        events.append("execute")
        assert events == ["reserve", "execute"]
        assert json.loads(environment["ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON"]) == {"synthetic": True}
        assert environment["ANANTA_MEET_MEDIA_TIMING"] == "1"
        assert environment["MEET_DIALOG_SOAK_SECONDS"] == profile.environment()["MEET_DIALOG_SOAK_SECONDS"]
        assert environment["PYTEST_ADDOPTS"] == profile.environment()["PYTEST_ADDOPTS"]
        assert all(environment[key] == value for key, value in profile.settings)
        assert "-n" in command and "0" in command
        assert profile.node in command
        assert timeout == profile.timeout_seconds
        if mode == "exception":
            raise RuntimeError("PRIVATE-MARKER")
        marker = "<skipped/>" if mode == "skip" else "<failure/>" if mode == "fail" else ""
        (log_path.parent / "junit.xml").write_text(f"<testsuite><testcase>{marker}</testcase></testsuite>")
        return 1 if mode == "fail" else 0

    output = tmp_path / "owned-report"
    code = run(
        output,
        tmp_path / "registry.sqlite",
        tmp_path,
        profile_name=profile_name,
        root=tmp_path,
        reserve=reserve,
        worker=worker,
    )
    report = json.loads((output / "report.json").read_text())
    assert code == (0 if mode == "pass" else 1)
    assert report["result"]["passed"] is (mode == "pass")
    assert report["identity"]["production_release_eligible"] is False
    assert "PRIVATE-MARKER" not in json.dumps(report)
    assert reserved.complete.call_args.kwargs == {"succeeded": mode == "pass"}


def test_unlisted_profile_fails_before_reading_inputs_or_reserving(tmp_path, monkeypatch):
    read, reserve = Mock(), Mock()
    monkeypatch.setattr("scripts.run_meet_test_gate.snapshot_repository", read)
    with pytest.raises(ValueError, match="profile_invalid"):
        run(tmp_path, tmp_path, tmp_path, profile_name="arbitrary-test", reserve=reserve)
    read.assert_not_called()
    reserve.assert_not_called()


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_profile_requires_every_actual_container_input_before_reservation(tmp_path, monkeypatch, profile_name):
    from scripts.meet_test_gate_profiles import select_profile

    profile = select_profile(profile_name)
    monkeypatch.setattr("scripts.run_meet_test_gate.snapshot_repository", lambda *_: ({}, (Path("test.py"),)))
    monkeypatch.setattr("scripts.run_meet_test_gate.frontend_digest", lambda _: "a" * 64)
    reserve = Mock()
    for missing in profile.image_inputs:
        inputs = dict.fromkeys(profile.image_inputs, "sha256:" + "a" * 64) | {"MEET_TEST_PUBLIC_DIR": "/synthetic"}
        inputs.pop(missing)
        monkeypatch.setattr("scripts.run_meet_test_gate.native_environment", lambda: inputs)
        with pytest.raises(ValueError, match="immutable_image_required"):
            run(tmp_path, tmp_path, tmp_path, profile_name=profile_name, reserve=reserve)
    reserve.assert_not_called()


def test_selected_soak_is_fixed_and_cannot_mutate_other_profile_environment():
    from scripts.meet_test_gate_profiles import select_profile

    long = select_profile("private-dialog-soak")
    assert long.environment()["MEET_DIALOG_SOAK_SECONDS"] == "7200"
    assert long.timeout_seconds == 7560 and long.node.endswith("[text]")
    changed = long.environment()
    changed["MEET_DIALOG_SOAK_SECONDS"] = "1"
    assert long.environment()["MEET_DIALOG_SOAK_SECONDS"] == "7200"
    assert select_profile("gpu-avatar").environment()["MEET_DIALOG_SOAK_SECONDS"] == "0"


def test_existing_output_is_not_overwritten_and_preflight_failure_never_reserves(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.run_meet_test_gate.snapshot_repository", Mock(side_effect=ValueError("unready")))
    reserve = Mock()
    with pytest.raises(ValueError, match="unready"):
        run(tmp_path, tmp_path / "registry.sqlite", tmp_path, reserve=reserve)
    reserve.assert_not_called()


def test_executor_timeout_terminates_only_its_owned_process_group(tmp_path, monkeypatch):
    process = Mock(pid=123456)
    process.wait.side_effect = [
        subprocess.TimeoutExpired("synthetic", 1),
        subprocess.TimeoutExpired("synthetic", 10),
        0,
    ]
    process.poll.return_value = None
    spawn, kill = Mock(return_value=process), Mock()
    monkeypatch.setattr("scripts.run_meet_test_gate.subprocess.Popen", spawn)
    monkeypatch.setattr("scripts.run_meet_test_gate.os.killpg", kill)
    with pytest.raises(subprocess.TimeoutExpired):
        execute(["synthetic"], {}, tmp_path / "test.log", root=tmp_path, timeout=1)
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert spawn.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert [call.args[0] for call in kill.call_args_list] == [123456, 123456]
    assert [call.kwargs["timeout"] for call in process.wait.call_args_list] == [1, 10, 5]
