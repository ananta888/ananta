from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.sources import sources_bp


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(sources_bp)
    return app


def _headers(role: str) -> dict[str, str]:
    token = generate_token(
        {
            "sub": f"{role}-operator",
            "role": role,
            "tenant_id": "tenant-a",
        },
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_source_reads_do_not_synchronize_or_mutate_builtins(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.routes.sources._sync_builtin_descriptors",
        lambda: (_ for _ in ()).throw(AssertionError("read must not mutate")),
    )
    monkeypatch.setattr(
        "agent.routes.sources._registry",
        lambda: SimpleNamespace(list_sources=lambda **_kwargs: []),
    )

    response = _app().test_client().get(
        "/sources",
        headers=_headers("user"),
    )

    assert response.status_code == 200
    assert response.get_json()["data"] == []


def test_source_mutations_are_fail_closed_admin_only() -> None:
    client = _app().test_client()
    mutations = (
        ("/sources/refresh", {}),
        ("/sources/source-a/refresh", {}),
        ("/sources/source-a/cache/clear", {}),
        ("/sources/import/open-notebook", {}),
        ("/sources/packs/default/bootstrap", {}),
        ("/sources/actions/sync-builtins", {}),
    )

    for path, payload in mutations:
        unauthenticated = client.post(path, json=payload)
        regular_user = client.post(
            path,
            json=payload,
            headers=_headers("user"),
        )

        assert unauthenticated.status_code == 401
        assert regular_user.status_code == 403


def test_admin_can_run_explicit_builtin_sync(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.routes.sources._sync_builtin_descriptors",
        lambda: 3,
    )

    response = _app().test_client().post(
        "/sources/actions/sync-builtins",
        json={},
        headers=_headers("admin"),
    )

    assert response.status_code == 200
    assert response.get_json()["data"] == {"status": "ok", "created": 3}
