"""Actual browser callbacks never retain renewal bodies or arbitrary console text."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.meet_media.companion_chat_diagnostics import (
    browser_console_diagnostic,
    renewal_status_diagnostic,
)


@pytest.mark.parametrize("status", [True, None, "private-token", 99, 600, 200.0])
def test_renewal_status_rejects_non_http_values(status):
    assert renewal_status_diagnostic(status) == "RENEW status=invalid"


def callbacks():
    source = Path(__file__).resolve().parents[2] / "worker/meet_media/companion.py"
    functions = [node for node in ast.walk(ast.parse(source.read_text()))
                 if isinstance(node, ast.FunctionDef) and node.name in {"_on_response", "_on_console"}]
    assert len(functions) == 2
    logs = []
    namespace = {"log": logs.append, "renewal_status_diagnostic": renewal_status_diagnostic,
                 "browser_console_diagnostic": browser_console_diagnostic}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace, logs


def test_actual_renewal_callback_never_reads_or_retains_the_response_body():
    functions, logs = callbacks()

    class Response:
        url = "https://synthetic.example/api/machine/sessions/renew"
        status = 200

        def text(self):
            pytest.fail("renewal body contains authority and must never be read")

    functions["_on_response"](Response())
    assert logs == ["RENEW status=200"]


def test_actual_console_callback_projects_only_fixed_markers():
    functions, logs = callbacks()
    for kind, text in [("error", "private-token"), ("warning", "private-room"),
                       ("log", "[e2eedbg] private-peer"), ("log", "private-chat")]:
        functions["_on_console"](SimpleNamespace(type=kind, text=text))
    assert logs == ["CONSOLE level=error event=browser", "CONSOLE level=warning event=browser",
                    "CONSOLE level=log event=e2ee"]


def test_observation_failure_never_logs_the_exception_or_blocks_the_callback():
    functions, logs = callbacks()

    class FailedObservation:
        url = "/api/machine/sessions/renew"

        def __getattr__(self, _name):
            raise RuntimeError("private-token")

    functions["_on_response"](FailedObservation())
    functions["_on_console"](FailedObservation())
    assert logs == ["RENEW event=observation_failed", "CONSOLE event=observation_failed"]
