import threading
import time

import pytest
import requests

from agent.common import http as http_module
from agent.common.http import (
    HttpClient,
    HttpTransportDeadlineExceeded,
    HttpTransportResponseLost,
)
from agent.common.lmstudio_request_registry import (
    clear_thread_context,
    set_thread_context,
)


class _JsonResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_http_client_get_falls_back_from_host_docker_internal(monkeypatch):
    client = HttpClient()
    calls: list[str] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "host.docker.internal" in url:
            raise requests.exceptions.ConnectionError("connection refused")
        return _JsonResponse({"ok": True, "url": url})

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr("agent.utils.get_host_gateway_ip", lambda: "172.17.0.1")

    result = client.get("http://host.docker.internal:11434/api/generate", silent=True)

    assert result == {"ok": True, "url": "http://172.17.0.1:11434/api/generate"}
    assert calls == [
        "http://host.docker.internal:11434/api/generate",
        "http://172.17.0.1:11434/api/generate",
    ]


def test_http_client_post_falls_back_from_host_docker_internal(monkeypatch):
    client = HttpClient()
    calls: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "host.docker.internal" in url:
            raise requests.exceptions.ConnectionError("connection refused")
        return _JsonResponse({"ok": True, "url": url, "json": json})

    monkeypatch.setattr(client.session, "post", fake_post)
    monkeypatch.setattr("agent.utils.get_host_gateway_ip", lambda: "172.17.0.1")

    result = client.post("http://host.docker.internal:1234/v1/chat/completions", data={"hello": "world"}, silent=True)

    assert result == {"ok": True, "url": "http://172.17.0.1:1234/v1/chat/completions", "json": {"hello": "world"}}
    assert calls == [
        "http://host.docker.internal:1234/v1/chat/completions",
        "http://172.17.0.1:1234/v1/chat/completions",
    ]


def test_tracked_post_transport_error_is_not_resent_untracked(monkeypatch):
    client = HttpClient()
    calls = []

    class FailingSession:
        def post(self, url, **_kwargs):
            calls.append(url)
            raise requests.exceptions.Timeout("timed out")

        def close(self):
            return None

    session = FailingSession()
    monkeypatch.setattr(http_module.requests, "Session", lambda: session)
    set_thread_context(None, "tracked-task")
    try:
        result = client.post(
            "http://worker:5001/tasks/tracked/step/execute",
            data={"task_id": "tracked-task"},
            silent=True,
        )
    finally:
        clear_thread_context()

    assert result is None
    assert calls == [
        "http://worker:5001/tasks/tracked/step/execute"
    ]


def test_post_header_wait_obeys_absolute_deadline(monkeypatch):
    client = HttpClient()

    class SlowSession:
        def __init__(self):
            self.closed = False

        def post(self, _url, **_kwargs):
            time.sleep(1.0)
            return _JsonResponse({"ok": True})

        def close(self):
            self.closed = True

    session = SlowSession()
    monkeypatch.setattr(http_module.requests, "Session", lambda: session)
    started = time.monotonic()

    with pytest.raises(HttpTransportDeadlineExceeded):
        client.post(
            "http://worker:5001/tasks/deadline/step/execute",
            data={"task_id": "deadline"},
            timeout=(0.01, 1.0),
            deadline_monotonic=time.monotonic() + 0.05,
        )

    assert time.monotonic() - started < 0.5
    assert session.closed is True


def test_post_deadline_closes_response_that_arrives_after_abort(
    monkeypatch,
):
    client = HttpClient()
    release_response = threading.Event()
    response_closed = threading.Event()

    class LateResponse(_JsonResponse):
        def close(self):
            response_closed.set()

    response = LateResponse({"ok": True})

    class SlowSession:
        def post(self, _url, **_kwargs):
            release_response.wait(timeout=1)
            return response

        def close(self):
            return None

    monkeypatch.setattr(
        http_module.requests,
        "Session",
        lambda: SlowSession(),
    )

    with pytest.raises(HttpTransportDeadlineExceeded):
        client.post(
            "http://worker:5001/tasks/deadline/step/execute",
            data={"task_id": "deadline"},
            deadline_monotonic=time.monotonic() + 0.05,
        )

    release_response.set()
    assert response_closed.wait(timeout=1)


def test_deadline_governed_post_never_retargets_host_gateway(
    monkeypatch,
):
    client = HttpClient()
    calls = []

    class FailingSession:
        def post(self, url, **_kwargs):
            calls.append(url)
            raise requests.exceptions.ConnectionError(
                "response connection dropped"
            )

        def close(self):
            return None

    monkeypatch.setattr(
        http_module.requests,
        "Session",
        lambda: FailingSession(),
    )
    gateway_calls = []
    monkeypatch.setattr(
        "agent.utils.get_host_gateway_ip",
        lambda: gateway_calls.append(True) or "172.17.0.1",
    )

    result = client.post(
        "http://host.docker.internal:5001/tasks/job/step/execute",
        data={"task_id": "job"},
        deadline_monotonic=time.monotonic() + 1.0,
        silent=True,
    )

    assert result is None
    assert calls == [
        "http://host.docker.internal:5001/tasks/job/step/execute"
    ]
    assert gateway_calls == []

    with pytest.raises(HttpTransportResponseLost):
        client.post(
            "http://host.docker.internal:5001/tasks/job/step/execute",
            data={"task_id": "job"},
            deadline_monotonic=time.monotonic() + 1.0,
            silent=True,
            raise_on_transport_error=True,
        )

    assert calls == [
        "http://host.docker.internal:5001/tasks/job/step/execute",
        "http://host.docker.internal:5001/tasks/job/step/execute",
    ]
    assert gateway_calls == []
