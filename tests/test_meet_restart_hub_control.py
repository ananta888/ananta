"""Test-only Hub controls cannot silently become an unguarded execution port."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from tests.meet_restart_hub_control import register_control
from tests.meet_restart_hub_runtime import validate_config


def configuration():
    return {
        "meeting_origin": "https://172.20.0.1",
        "worker_origin": "http://172.20.0.3:8094",
        "room_id": "room-" + "a" * 18,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"room_id": "foreign"},
        {"extra": True},
        {"worker_origin": None},
        {"worker_origin": []},
        {"worker_origin": "http://172.20.0.3:80"},
        {"worker_origin": "http://172.20.0.3:8094/path"},
        {"worker_origin": "http://user:secret@172.20.0.3:8094"},
        {"meeting_origin": "http://172.20.0.1"},
        {"meeting_origin": "https://8.8.8.8"},
        {"meeting_origin": "https://172.20.0.1?grant=anything"},
        {"meeting_origin": "https://172.20.0.1\n"},
        {"meeting_origin": "https://localhost"},
    ],
)
def test_private_hub_config_is_closed_before_resources(changes):
    with pytest.raises(ValueError):
        validate_config(configuration() | changes)


def test_private_hub_config_retains_exact_fixed_origins():
    value = configuration()
    assert validate_config(value) is value


@pytest.mark.parametrize("key", [None, b"", b"x" * 31, b"x" * 33, "x" * 32])
def test_empty_or_malformed_control_key_never_registers_routes(key):
    app = Flask(__name__)
    with pytest.raises(ValueError, match="control_key_invalid"):
        register_control(app, Mock(), object(), key, task_count=lambda: 0)
    assert len(list(app.url_map.iter_rules())) == 1  # Only Flask's static route.


def test_control_requires_exact_key_no_query_no_body_and_has_no_arbitrary_options():
    app, service, principal = Flask(__name__), Mock(), object()
    task_count = Mock(return_value=0)
    register_control(app, service, principal, b"x" * 32, task_count=task_count)
    client = app.test_client()
    headers = {"X-Test-Hub-Control": (b"x" * 32).hex()}
    for path, kwargs in [
        ("/__test/start", {}),
        ("/__test/start", {"headers": {"X-Test-Hub-Control": "wrong"}}),
        ("/__test/start?duration=7200", {"headers": headers}),
        ("/__test/start", {"headers": headers, "data": "{}"}),
    ]:
        assert client.post(path, **kwargs).status_code == 403
    service.start.assert_not_called()
    task_count.assert_not_called()
    service.start.return_value = {"task_id": "synthetic-task"}
    assert client.post("/__test/start", headers=headers).json == {"task_id": "synthetic-task"}
    service.start.assert_called_once_with(
        principal,
        "synthetic",
        {
            "capabilities": ["video.receive"],
            "duration_seconds": 120,
            "chat_mode": "off",
        },
        parent="meet-test-parent",
    )
    task_count.return_value = 1
    assert client.post("/__test/start", headers=headers).status_code == 409
    assert service.start.call_count == 1


def test_private_controls_do_not_collide_with_native_hub_health_endpoint():
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {"native": True}

    register_control(app, Mock(), object(), b"x" * 32, task_count=lambda: 0)
    client = app.test_client()
    assert client.get("/health").json == {"native": True}
    assert client.get("/__test/health").json == {"ready": True}


def test_terminal_task_count_is_not_the_active_only_ui_list():
    app, service = Flask(__name__), Mock()
    service.tasks.get_by_id.return_value = SimpleNamespace(
        tenant_id="synthetic",
        project_id="synthetic",
        task_kind="meet_dialog_session",
        status="failed",
        worker_execution_context={"meet_dialog": {"deadline": 100}},
        history=[],
    )
    register_control(app, service, object(), b"x" * 32, task_count=lambda: 1)
    client = app.test_client()
    headers = {"X-Test-Hub-Control": (b"x" * 32).hex()}
    value = client.get("/__test/status/" + "a" * 36, headers=headers).json
    assert value["status"] == "failed" and value["dialog_tasks"] == 1
    assert client.post("/__test/start", headers=headers).status_code == 409
    service.start.assert_not_called()
    service.tasks.list_page.assert_not_called()


@pytest.mark.timeout(15)
def test_runtime_without_explicit_opt_in_exits_before_loading_hub_or_files():
    env = dict(os.environ)
    env.pop("MEET_HUB_RESTART_GATE", None)
    result = subprocess.run(
        [sys.executable, "-m", "tests.meet_restart_hub_runtime"], env=env, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout) == {"error": "test_hub_runtime_failed", "type": "ValueError"}
