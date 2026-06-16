"""Tests for snake event broadcasting and SSE endpoint.

Covers:
- broadcast_snake_event enqueues events per snake
- Queue backpressure drops oldest events when full
- SSE endpoint streams events with correct mimetype and auth
- drop_snake_queue removes the queue for a snake
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clean_queues():
    """Reset the module-level queues before each test."""
    import agent.routes.snake_event_broadcaster as broadcaster

    with broadcaster._LOCK:
        broadcaster._QUEUES.clear()
    yield
    with broadcaster._LOCK:
        broadcaster._QUEUES.clear()


class TestBroadcastSnakeEvent:
    """Event broadcaster enqueue / backpressure behaviour."""

    def test_event_is_queued_for_snake(self):
        from agent.routes.snake_event_broadcaster import (
            broadcast_snake_event,
            get_snake_event,
        )

        broadcast_snake_event("snake-a", "guide", {"steps": []})
        evt = get_snake_event("snake-a", timeout=0.1)

        assert evt is not None
        assert evt["type"] == "guide"
        assert evt["payload"] == {"steps": []}
        assert "ts" in evt

    def test_queues_are_isolated_per_snake(self):
        from agent.routes.snake_event_broadcaster import (
            broadcast_snake_event,
            get_snake_event,
        )

        broadcast_snake_event("snake-a", "guide", {"steps": []})
        broadcast_snake_event("snake-b", "candidates", {"candidates": []})

        assert get_snake_event("snake-a", timeout=0.1)["type"] == "guide"
        assert get_snake_event("snake-b", timeout=0.1)["type"] == "candidates"

    def test_empty_snake_id_is_noop(self):
        from agent.routes.snake_event_broadcaster import (
            broadcast_snake_event,
            get_snake_event,
        )

        broadcast_snake_event("", "guide", {"steps": []})
        evt = get_snake_event("", timeout=0.05)
        assert evt is None

    def test_queue_drops_oldest_when_full(self):
        import agent.routes.snake_event_broadcaster as broadcaster

        broadcaster._MAX_QUEUE_SIZE = 3
        try:
            from agent.routes.snake_event_broadcaster import (
                broadcast_snake_event,
                get_snake_event,
            )

            for i in range(5):
                broadcast_snake_event("snake-a", "tick", {"n": i})

            values = []
            for _ in range(3):
                evt = get_snake_event("snake-a", timeout=0.1)
                if evt:
                    values.append(evt["payload"]["n"])

            # Oldest two events (0,1) should have been dropped
            assert values == [2, 3, 4]
        finally:
            broadcaster._MAX_QUEUE_SIZE = 128


class TestDropSnakeQueue:
    """drop_snake_queue removes the per-snake queue."""

    def test_drop_removes_queue(self):
        from agent.routes.snake_event_broadcaster import (
            broadcast_snake_event,
            drop_snake_queue,
            get_snake_event,
        )

        broadcast_snake_event("snake-x", "guide", {"steps": []})
        drop_snake_queue("snake-x")
        evt = get_snake_event("snake-x", timeout=0.05)
        assert evt is None


class TestSnakeEventsStreamEndpoint:
    """Integration tests for GET /snakes/<id>/events/stream."""

    @pytest.fixture
    def client(self):
        from agent.routes.snakes import _snakes, snakes_bp
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(snakes_bp)

        # Seed a snake with known token
        _snakes["test-snake"] = {
            "id": "test-snake",
            "token": "secret-token",
            "active": True,
            "name": "test",
            "role": "viewer",
            "color": "mint",
        }

        # Import broadcaster late to pick up the registered snake
        import agent.routes.snake_event_broadcaster as broadcaster

        with broadcaster._LOCK:
            broadcaster._QUEUES.clear()

        yield app.test_client()

        with broadcaster._LOCK:
            broadcaster._QUEUES.clear()
        _snakes.pop("test-snake", None)

    def test_stream_requires_valid_token(self, client):
        resp = client.get("/snakes/test-snake/events/stream?token=bad")
        assert resp.status_code == 401

    def test_stream_returns_event_stream_mimetype(self, client):
        import agent.routes.snake_event_broadcaster as broadcaster

        resp = client.get("/snakes/test-snake/events/stream?token=secret-token")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"

        # Push an event and read it from the streaming response
        broadcaster.broadcast_snake_event("test-snake", "guide", {"steps": [{"waypoint": "x"}]})

        # read the response generator manually
        chunks = []
        start = time.time()
        for chunk in resp.response:
            chunks.append(chunk.decode("utf-8"))
            if "data:" in "".join(chunks):
                break
            if time.time() - start > 2:
                pytest.fail("timed out waiting for SSE event")

        body = "".join(chunks)
        assert "data:" in body
        parsed = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        assert parsed["type"] == "guide"
        assert parsed["payload"]["steps"][0]["waypoint"] == "x"
