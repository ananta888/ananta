from __future__ import annotations


def test_sources_api_unknown_source_returns_404(client, admin_auth_header):
    res = client.get("/sources/unknown-source", headers=admin_auth_header)
    assert res.status_code == 404


def test_sources_api_citation_short_format(client, admin_auth_header):
    synced = client.post("/sources/actions/sync-builtins", headers=admin_auth_header, json={})
    assert synced.status_code == 200
    res = client.get("/sources/keycloak-official-docs/citation?format=short", headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    assert "short" in data
    assert data["rendered"] == data["short"]
