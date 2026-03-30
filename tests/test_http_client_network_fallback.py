import requests

from agent.common.http import HttpClient


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
