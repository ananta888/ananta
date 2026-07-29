import json

import pytest

from agent.routes.tasks import utils as task_utils


def test_get_local_task_status_returns_none_when_missing(monkeypatch):
    class StubRepo:
        @staticmethod
        def get_by_id(_tid):
            return None

    monkeypatch.setattr(task_utils, "task_repo", StubRepo())
    assert task_utils._get_local_task_status("T-missing") is None


def test_update_local_task_status_delegates_to_runtime_service(monkeypatch):
    captured = {}

    def _fake_update_local_task_status(tid, status, **kwargs):
        captured["tid"] = tid
        captured["status"] = status
        captured["kwargs"] = dict(kwargs)

    monkeypatch.setattr(task_utils, "update_local_task_status", _fake_update_local_task_status)
    task_utils._update_local_task_status("T-1", "in_progress", title="Test task")

    assert captured["tid"] == "T-1"
    assert captured["status"] == "in_progress"
    assert captured["kwargs"]["title"] == "Test task"


def test_forward_to_worker_builds_url_and_auth_header(monkeypatch):
    calls = []

    def fake_http_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers or {}, "timeout": timeout})
        return {"ok": True}

    monkeypatch.setattr(task_utils, "_http_post", fake_http_post)

    result = task_utils._forward_to_worker(
        "http://worker.local/",
        "/step/execute",
        {"command": "echo hi"},
        token="tok-123",
    )

    assert result == {"ok": True}
    assert calls[0]["url"] == "http://worker.local/step/execute"
    assert calls[0]["data"] == {"command": "echo hi"}
    assert calls[0]["headers"]["Authorization"] == "Bearer tok-123"
    assert calls[0]["timeout"] is not None


def test_forward_to_worker_extends_timeout_for_step_propose(monkeypatch, app):
    calls = []

    def fake_http_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        return {"ok": True}

    monkeypatch.setattr(task_utils, "_http_post", fake_http_post)

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "command_timeout": 75,
        }
        result = task_utils._forward_to_worker(
            "http://worker.local/",
            "/tasks/T-1/step/propose",
            {"prompt": "x"},
            token="tok-123",
        )

    assert result == {"ok": True}
    assert calls[0]["url"] == "http://worker.local/tasks/T-1/step/propose"
    assert calls[0]["timeout"] == 195


class StreamingResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.json_calls = 0

    def iter_content(
        self,
        *,
        chunk_size: int,
        decode_unicode: bool,
    ):
        assert chunk_size > 0
        assert decode_unicode is False
        yield from self.chunks

    def json(self):
        self.json_calls += 1
        raise AssertionError("streamed Recovery body used response.json")

    def close(self) -> None:
        self.closed = True


def _vector_dispatch() -> dict:
    return {
        "schema": "ananta.vector_index_task.v1",
        "dispatch": {
            "schema": "ananta.vector_index_task_dispatch.v1",
        },
    }


def test_recovery_forward_streams_and_bounds_body_before_json_parse(
    monkeypatch,
):
    payload = {
        "status": "success",
        "data": {
            "status": "completed",
            "artifacts": [],
        },
    }
    encoded = json.dumps(payload).encode("utf-8")
    response = StreamingResponse(
        [encoded[:7], encoded[7:]]
    )
    calls = []

    def fake_http_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(
        task_utils,
        "_http_post",
        fake_http_post,
    )

    result = task_utils._forward_to_worker(
        "http://worker.local",
        "/tasks/recovery-child/step/execute",
        {
            "dispatch_lease_token": "lease-token",
            "dispatch_lease_phase": "execute",
        },
        token="worker-token",
    )

    assert result == payload
    assert calls[0]["stream"] is True
    assert calls[0]["return_response"] is True
    assert response.json_calls == 0
    assert response.closed is True


def test_vector_forward_streams_and_bounds_body_before_json_parse(
    monkeypatch,
):
    payload = {
        "status": "success",
        "data": {
            "schema": "ananta.vector_index_task_result.v1",
            "status": "completed",
        },
    }
    encoded = json.dumps(payload).encode("utf-8")
    response = StreamingResponse([encoded[:9], encoded[9:]])
    calls = []

    def fake_http_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(task_utils, "_http_post", fake_http_post)

    result = task_utils._forward_to_worker(
        "http://worker.local",
        "/tasks/vector-index/step/execute",
        {"vector_index_dispatch": _vector_dispatch()},
        token="worker-token",
    )

    assert result == payload
    assert calls[0]["stream"] is True
    assert calls[0]["allow_redirects"] is False
    assert response.json_calls == 0
    assert response.closed is True


def test_vector_forward_rejects_oversized_and_malformed_streams(
    monkeypatch,
):
    monkeypatch.setattr(
        task_utils,
        "MAX_VECTOR_INDEX_WORKER_RESULT_BYTES",
        8,
    )
    oversized = StreamingResponse(
        [b'{"data"', b':"oversized"}']
    )
    monkeypatch.setattr(
        task_utils,
        "_http_post",
        lambda _url, **_kwargs: oversized,
    )

    with pytest.raises(
        ValueError,
        match="vector_index_worker_response_too_large",
    ):
        task_utils._forward_to_worker(
            "http://worker.local",
            "/tasks/vector-index/step/execute",
            {"vector_index_dispatch": _vector_dispatch()},
            token="worker-token",
        )

    assert oversized.json_calls == 0
    assert oversized.closed is True

    monkeypatch.setattr(
        task_utils,
        "MAX_VECTOR_INDEX_WORKER_RESULT_BYTES",
        65_536,
    )
    malformed = StreamingResponse([b"{not-json"])
    monkeypatch.setattr(
        task_utils,
        "_http_post",
        lambda _url, **_kwargs: malformed,
    )
    with pytest.raises(
        ValueError,
        match="vector_index_worker_response_json_invalid",
    ):
        task_utils._forward_to_worker(
            "http://worker.local",
            "/tasks/vector-index/step/execute",
            {"vector_index_dispatch": _vector_dispatch()},
            token="worker-token",
        )


def test_worker_forward_rejects_redirect_without_following_it(
    monkeypatch,
):
    redirect = StreamingResponse([])
    redirect.status_code = 307
    redirect.headers = {"Location": "http://attacker.test/capture"}
    calls = []

    def fake_http_post(url, **kwargs):
        calls.append((url, kwargs))
        return redirect

    monkeypatch.setattr(task_utils, "_http_post", fake_http_post)

    with pytest.raises(
        ValueError,
        match="worker_forward_redirect_forbidden",
    ):
        task_utils._forward_to_worker(
            "http://worker.local",
            "/tasks/vector-index/step/execute",
            {"vector_index_dispatch": _vector_dispatch()},
            token="worker-token",
        )

    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert redirect.closed is True


def test_recovery_forward_rejects_oversized_stream_before_json_parse(
    monkeypatch,
):
    monkeypatch.setattr(
        task_utils,
        "MAX_RECOVERY_FORWARD_RESPONSE_BYTES",
        8,
    )
    response = StreamingResponse(
        [b'{"data"', b':"oversized"}']
    )
    monkeypatch.setattr(
        task_utils,
        "_http_post",
        lambda _url, **_kwargs: response,
    )

    with pytest.raises(
        ValueError,
        match="recovery_worker_response_too_large",
    ):
        task_utils._forward_to_worker(
            "http://worker.local",
            "/tasks/recovery-child/step/execute",
            {"dispatch_lease_token": "lease-token"},
            token="worker-token",
        )

    assert response.json_calls == 0
    assert response.closed is True
