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


def test_http_get_timeout_then_success(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(url, timeout=10.0):
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.URLError("timeout")
        return _MockResponse(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_get("http://example.test/unstable", retries=3, delay=0)
    assert calls["n"] == 2
    assert data == {"ok": True}


def test_http_get_invalid_json_returns_text(monkeypatch):
    def fake_urlopen(url, timeout=10.0):
        return _MockResponse(b"{invalid-json}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_get("http://example.test/invalid", retries=1, delay=0)
    assert isinstance(data, str)
    assert data == "{invalid-json}"


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


def test_http_post_form_and_headers(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=10.0):
        # capture headers and body
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _MockResponse(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    data = http_post(
        "http://example.test/form",
        {"a": 1, "b": "x"},
        form=True,
        headers={"X-Test": "1"},
        retries=1,
        delay=0,
    )
    assert data == "ok"
    # When form=True, client should not force application/json
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    assert hdrs.get("content-type") != "application/json"
    assert hdrs.get("x-test") == "1"
    # Body must be urlencoded
    assert b"a=1" in captured["body"] and b"b=x" in captured["body"]


def test_http_post_headers_merge_for_json(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=10.0):
        captured["headers"] = dict(req.header_items())
        return _MockResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _ = http_post(
        "http://example.test/json",
        {"a": 1},
        headers={"Authorization": "Bearer abc"},
        retries=1,
        delay=0,
    )
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    assert hdrs.get("content-type") == "application/json"
    assert hdrs.get("authorization") == "Bearer abc"



def test_http_get_passes_timeout(monkeypatch):
    captured = {"timeout": None}

    def fake_urlopen(url, timeout=10.0):
        captured["timeout"] = timeout
        return _MockResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _ = http_get("http://example.test/timeout", retries=1, delay=0, timeout=2.5)
    assert captured["timeout"] == 2.5



def test_http_post_passes_timeout(monkeypatch):
    captured = {"timeout": None}

    def fake_urlopen(req, timeout=10.0):
        captured["timeout"] = timeout
        return _MockResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    _ = http_post("http://example.test/tout", {"a": 1}, retries=1, delay=0, timeout=3.3)
    assert captured["timeout"] == 3.3
