"""An aborted model call really ends: the connection is shut down, the server sees the disconnect."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from agent.common.cancellable_session import CancellableSession


@pytest.fixture
def slow_server():
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            for _ in range(200):  # "generating" for up to 20 s; stops when the client is gone
                time.sleep(0.1)
                try:
                    self.connection.send(b"")
                    if self.connection.recv(1, 0x40) == b"":  # MSG_DONTWAIT: peer closed
                        disconnected.set()
                        return
                except BlockingIOError:
                    continue
                except OSError:
                    disconnected.set()
                    return

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", disconnected
    server.shutdown()


def test_close_from_another_thread_stops_the_request_in_flight(slow_server):
    url, disconnected = slow_server
    session = CancellableSession()
    outcome = {}

    def call():
        started = time.monotonic()
        try:
            session.post(url + "/v1/chat/completions", json={"x": 1}, timeout=30)
        except requests.exceptions.RequestException as exc:
            outcome["error"] = type(exc).__name__
        outcome["seconds"] = time.monotonic() - started

    worker = threading.Thread(target=call)
    worker.start()
    time.sleep(0.5)
    session.close()
    worker.join(5)
    assert not worker.is_alive() and "error" in outcome and outcome["seconds"] < 3
    assert disconnected.wait(3)  # the server noticed and stopped "generating"


def test_cancelling_a_task_closes_its_registered_sessions(slow_server):
    from agent.common import lmstudio_request_registry as registry

    url, disconnected = slow_server
    outcome = {}

    def call():
        registry.set_thread_context(None, "task-cancel-1")
        session, key = registry.create_and_register_session()
        try:
            session.post(url + "/x", json={}, timeout=30)
        except requests.exceptions.RequestException as exc:
            outcome["error"] = type(exc).__name__
        finally:
            registry.release_session(key, session)
            registry.clear_thread_context()

    worker = threading.Thread(target=call)
    worker.start()
    time.sleep(0.5)
    assert registry.cancel_task("task-cancel-1") == 1
    worker.join(5)
    assert "error" in outcome and disconnected.wait(3)
    registry.set_thread_context(None, "task-cancel-1")  # a new attempt of the task starts uncancelled
    assert not registry.is_cancelled(None, "task-cancel-1")
    registry.clear_thread_context()


def test_an_existing_session_can_be_made_cancellable(slow_server):
    from agent.common.cancellable_session import make_cancellable

    url, disconnected = slow_server
    session = make_cancellable(requests.Session())
    assert make_cancellable("not a session") == "not a session"
    worker = threading.Thread(target=lambda: _swallow(lambda: session.post(url + "/x", json={}, timeout=30)))
    worker.start()
    time.sleep(0.5)
    session.close()
    worker.join(5)
    assert not worker.is_alive() and disconnected.wait(3)


def _swallow(call):
    try:
        call()
    except requests.exceptions.RequestException:
        pass
