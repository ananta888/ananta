from __future__ import annotations


class _Response:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")
        self.headers = {"ETag": "x", "Last-Modified": "y", "Cache-Control": "max-age=60"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_sources_routes_list_get_citation_and_snapshots(client, admin_auth_header, monkeypatch, tmp_path) -> None:
    from agent.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    res_list = client.get("/sources", headers=admin_auth_header)
    assert res_list.status_code == 200
    data = res_list.json["data"]
    assert any(item["source_id"] == "keycloak-official-docs" for item in data)

    res_get = client.get("/sources/keycloak-official-docs", headers=admin_auth_header)
    assert res_get.status_code == 200
    assert res_get.json["data"]["source_id"] == "keycloak-official-docs"

    res_cite = client.get("/sources/keycloak-official-docs/citation", headers=admin_auth_header)
    assert res_cite.status_code == 200
    assert "keycloak" in str(res_cite.json["data"]["human_readable"]).lower()

    res_snapshots = client.get("/sources/keycloak-official-docs/snapshots", headers=admin_auth_header)
    assert res_snapshots.status_code == 200
    assert isinstance(res_snapshots.json["data"], list)


def test_sources_refresh_routes(client, admin_auth_header, monkeypatch, tmp_path) -> None:
    from agent.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response("<html><body>Keycloak docs text " * 300 + "</body></html>"))

    keycloak_refresh = client.post("/sources/keycloak-official-docs/refresh", headers=admin_auth_header, json={})
    assert keycloak_refresh.status_code == 200
    assert keycloak_refresh.json["data"]["status"] == "ok"
    assert keycloak_refresh.json["data"]["report"]["snapshot"]["status"] in {"indexed", "validating"}

    wiki_refresh = client.post("/sources/wikimedia-wikipedia-initial-dump/refresh", headers=admin_auth_header, json={})
    assert wiki_refresh.status_code == 200
    assert wiki_refresh.json["data"]["status"] == "queued"
