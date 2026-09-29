"""Actual Node classifier and Python projection expose only allowlisted categories."""

import json
import subprocess

import pytest

from tests.meet_browser_launch_diagnostic import CLASSIFY_LAUNCH_FAILURE, launch_failure


@pytest.mark.parametrize(
    "error,code",
    [
        ({"name": "TimeoutError", "message": "private URL and launch args"}, "timeout"),
        ({"message": "Failed to move to new namespace: private detail"}, "sandbox"),
        ({"message": "No usable sandbox! private detail"}, "sandbox"),
        ({"message": "pthread_create: Resource temporarily unavailable"}, "resource"),
        ({"message": "Cannot allocate memory /private/path"}, "resource"),
        ({"message": "No space left on device /private/path"}, "resource"),
        ({"message": "Executable doesn't exist at /private/path"}, "executable"),
        ({"message": "private unrecognized error"}, "unknown"),
        ({"message": "x" * 32768 + "No usable sandbox"}, "unknown"),
        ({"message": {"private": "object"}}, "unknown"),
        (None, "unknown"),
    ],
)
def test_real_node_classification_is_bounded_and_redacted(error, code):
    script = CLASSIFY_LAUNCH_FAILURE + ";console.log(classifyLaunchFailure(JSON.parse(process.argv[1])));"
    result = subprocess.run(["node", "-e", script, json.dumps(error)], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0 and result.stdout == code + "\n" and result.stderr == ""
    assert launch_failure("test_browser_launch_failed:" + code) == "test_browser_sandbox_launch_failed_" + code


@pytest.mark.parametrize(
    "line", ["", "private log", "test_browser_launch_failed:private", "test_browser_launch_failed:timeout extra"]
)
def test_arbitrary_browser_output_is_not_projected(line):
    assert launch_failure(line) is None


def test_old_fixed_launch_error_remains_compatible():
    assert launch_failure("test_browser_launch_failed") == "test_browser_sandbox_launch_failed"
