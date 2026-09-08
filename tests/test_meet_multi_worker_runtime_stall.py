"""Exact owned-target and closed-result checks for the packaged stall probe."""

import json
from unittest.mock import Mock

import pytest

from tests.meet_multi_worker_runtime_stall import STALL_PROBE, stall_owned_runtime
from tests.test_meet_multi_worker_crash import fixture


@pytest.mark.parametrize("change", [None, "running", "descendants", "elapsed", "kind", "unknown"])
def test_stall_probe_must_keep_container_live_and_report_exact_bounded_cleanup(change):
    container, row = fixture()
    result = {"runtime_frozen": True, "known_descendants": 5, "active_descendants_after": 0, "stop_ms": 500}
    if change == "descendants":
        result["active_descendants_after"] = 1
    elif change == "elapsed":
        result["stop_ms"] = 4500
    elif change == "kind":
        result["known_descendants"] = True
    elif change == "unknown":
        result["private"] = "not an allowed result"
    container.command = Mock(
        side_effect=[json.dumps([row]), json.dumps(result), json.dumps({"Running": change != "running"}), ""]
    )
    report = Mock()
    if change is None:
        stall_owned_runtime(container, report)
        assert report.call_args.args[1]["container_remains_live"] is True
        assert container.command.call_args_list[1].args == ("exec", row["Id"], "python", "-S", "-c", STALL_PROBE)
        assert container.command.call_args.args == ("exec", row["Id"], "python", "-S", "-m", "worker.meet_media.health")
    else:
        with pytest.raises(AssertionError):
            stall_owned_runtime(container, report)
        report.assert_not_called()


def test_unowned_container_never_receives_a_probe_and_probe_syntax_is_valid():
    container, _ = fixture()
    container.created = False
    container.command = Mock()
    with pytest.raises(ValueError, match="target_invalid"):
        stall_owned_runtime(container, Mock())
    container.command.assert_not_called()
    compile(STALL_PROBE, "owned-runtime-stall-probe", "exec")
