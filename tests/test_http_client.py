import json
import types
import urllib.error
import urllib.request

import pytest

from common.http_client import http_get, http_post


class _MockResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_get_json_success(monkeypatch):
    def fake_urlopen(url, timeout=10.0):
        assert isinstance(url, str)
        return _MockResponse(b'{"ok": true, "v": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_get("http://example.test/api", retries=1, delay=0)
    assert isinstance(data, dict)
    assert data["ok"] is True
    assert data["v"] == 1


def test_http_get_text_success(monkeypatch):
    def fake_urlopen(url, timeout=10.0):
        return _MockResponse(b"plain text response")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_get("http://example.test/text", retries=1, delay=0)
    assert data == "plain text response"


def test_http_get_retry_and_fail_returns_none(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(url, timeout=10.0):
        calls["n"] += 1
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_get("http://example.test/down", retries=2, delay=0)
    assert calls["n"] == 2
    assert data is None


def test_http_post_json_success(monkeypatch):
    def fake_urlopen(req, timeout=10.0):
        assert isinstance(req, urllib.request.Request)
        body = req.data
        # Should be JSON by default
        parsed = json.loads(body.decode())
        assert parsed == {"a": 1}
        return _MockResponse(b'{"status": "ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_post("http://example.test/create", {"a": 1}, retries=1, delay=0)
    assert data == {"status": "ok"}


def test_http_post_text_success(monkeypatch):
    def fake_urlopen(req, timeout=10.0):
        return _MockResponse(b"created")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_post("http://example.test/create", {"a": 1}, retries=1, delay=0)
    assert data == "created"


def test_http_post_retry_fail_returns_none(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=10.0):
        calls["n"] += 1
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_post("http://example.test/create", {"a": 1}, retries=3, delay=0)
    assert calls["n"] == 3
    assert data is None
