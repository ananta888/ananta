import io
import logging
import urllib.error
import urllib.request

from controller.controller import fetch_issues


class DummyResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass


def test_fetch_issues_retry(monkeypatch, caplog):
    calls = {"n": 0}

    def fake_urlopen(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("fail")
        return DummyResponse(b"[{\"title\":\"t\",\"number\":1}]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with caplog.at_level(logging.WARNING):
        issues = fetch_issues("owner/repo", retries=2, delay=0)

    assert issues == [{"title": "t", "number": 1}]
    assert calls["n"] == 2
    assert "attempt 1/2" in caplog.text
