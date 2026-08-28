from __future__ import annotations

from collections import defaultdict

import pytest
from pydantic import BaseModel

import agent.routes.config.providers as provider_routes
import agent.routes.tasks.kanban as kanban_routes
from agent.services.surface_rate_limit_policy import (
    KANBAN_EVENT_RECONNECT,
    KANBAN_WRITE,
    MODEL_CATALOG_REFRESH,
    MODEL_DEFAULT_SELECTION,
    SurfaceRateLimitPolicy,
    rate_limit_subject,
    resolve_surface_rate_limit,
    surface_rate_limit_policy,
)


class _DeterministicLimiter:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = defaultdict(int)

    def allow_request(
        self,
        *,
        namespace: str,
        subject: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        key = (namespace, subject)
        self.counts[key] += 1
        return self.counts[key] <= limit

    def clear_namespace(self, namespace: str) -> None:
        self.counts = defaultdict(
            int,
            {
                key: count
                for key, count in self.counts.items()
                if key[0] != namespace
            },
        )

    def clear_all(self) -> None:
        self.counts.clear()


class _WireCatalog:
    models: tuple[object, ...] = ()

    def to_wire(self):
        return {
            "schema": "ananta.model-catalog.v1",
            "catalog_digest": "0" * 64,
            "models": [],
            "providers": [],
        }


class _CatalogService:
    def versioned_catalog(self, query):
        return _WireCatalog()


class _Command:
    @classmethod
    def model_validate(cls, value):
        return object()


class _Selection(BaseModel):
    provider_id: str
    model_id: str


class _Selector:
    def __init__(self, **kwargs) -> None:
        pass

    def select(self, command, *, query):
        return _Selection(provider_id="local", model_id="model")


class _BoardResult(BaseModel):
    id: str = "hub"


class _KanbanService:
    def create_board(self, command, principal):
        return _BoardResult()

    def get_board(self, board_id, principal):
        return _BoardResult(id=board_id)


@pytest.fixture
def deterministic_surface_limiter(monkeypatch):
    limiter = _DeterministicLimiter()
    monkeypatch.setattr(surface_rate_limit_policy, "_limiter", limiter)
    return limiter


def _enable_model_catalog(app) -> None:
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG", {}) or {}),
        "feature_angular_model_dashboard_enabled": True,
    }


def _enable_kanban(app) -> None:
    app.config["KANBAN_API_ENABLED"] = True
    app.config["KANBAN_WRITE_ENABLED"] = True


def test_policy_is_per_identity_and_namespace() -> None:
    limiter = _DeterministicLimiter()
    policy = SurfaceRateLimitPolicy(limiter)
    config = {
        "SURFACE_RATE_LIMITS": {
            KANBAN_WRITE: {"limit": 1, "window_seconds": 37}
        }
    }

    first = policy.consume(
        config=config,
        namespace=KANBAN_WRITE,
        auth_payload={"sub": "alice", "tenant_id": "tenant-a"},
    )
    denied = policy.consume(
        config=config,
        namespace=KANBAN_WRITE,
        auth_payload={"sub": "alice", "tenant_id": "tenant-a"},
    )
    other_identity = policy.consume(
        config=config,
        namespace=KANBAN_WRITE,
        auth_payload={"sub": "bob", "tenant_id": "tenant-a"},
    )
    other_namespace = policy.consume(
        config={
            "SURFACE_RATE_LIMITS": {
                MODEL_CATALOG_REFRESH: {
                    "limit": 1,
                    "window_seconds": 37,
                }
            }
        },
        namespace=MODEL_CATALOG_REFRESH,
        auth_payload={"sub": "alice", "tenant_id": "tenant-a"},
    )

    assert first.allowed is True
    assert denied.allowed is False
    assert denied.retry_after_seconds == 37
    assert other_identity.allowed is True
    assert other_namespace.allowed is True


def test_identity_key_is_tenant_aware_and_does_not_expose_claims() -> None:
    first = rate_limit_subject(
        auth_payload={"sub": "alice@example.test", "tenant_id": "tenant-a"}
    )
    second = rate_limit_subject(
        auth_payload={"sub": "alice@example.test", "tenant_id": "tenant-b"}
    )

    assert first != second
    assert "alice" not in first
    assert len(first) == 64


def test_invalid_policy_values_fall_back_to_positive_defaults(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANANTA_KANBAN_WRITE_RATE_LIMIT", raising=False)
    monkeypatch.delenv(
        "ANANTA_KANBAN_WRITE_RATE_LIMIT_WINDOW_SECONDS",
        raising=False,
    )
    resolved = resolve_surface_rate_limit(
        {
            "SURFACE_RATE_LIMITS": {
                KANBAN_WRITE: {"limit": 0, "window_seconds": -1}
            }
        },
        KANBAN_WRITE,
    )

    assert resolved.limit == 120
    assert resolved.window_seconds == 60


def test_catalog_refresh_has_stable_429_and_retry_after(
    app,
    client,
    admin_auth_header,
    monkeypatch,
    deterministic_surface_limiter,
) -> None:
    _enable_model_catalog(app)
    limit = 3
    app.config["SURFACE_RATE_LIMITS"] = {
        MODEL_CATALOG_REFRESH: {
            "limit": limit,
            "window_seconds": 41,
        }
    }
    monkeypatch.setattr(
        provider_routes,
        "_model_catalog_service",
        lambda: _CatalogService(),
    )

    allowed = [
        client.post(
            "/models/catalog/v1/refresh",
            headers=admin_auth_header,
            json={},
        )
        for _ in range(limit)
    ]
    denied = client.post(
        "/models/catalog/v1/refresh",
        headers=admin_auth_header,
        json={},
    )

    assert all(response.status_code == 200 for response in allowed)
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "41"
    assert denied.get_json()["message"] == "rate_limit_exceeded"


def test_default_selection_has_stable_429_and_retry_after(
    app,
    client,
    admin_auth_header,
    monkeypatch,
    deterministic_surface_limiter,
) -> None:
    _enable_model_catalog(app)
    limit = 3
    app.config["SURFACE_RATE_LIMITS"] = {
        MODEL_DEFAULT_SELECTION: {
            "limit": limit,
            "window_seconds": 43,
        }
    }
    monkeypatch.setattr(provider_routes, "ModelDefaultSelectionCommand", _Command)
    monkeypatch.setattr(
        provider_routes,
        "build_persisted_default_selection_service",
        lambda **_kwargs: _Selector(),
    )
    monkeypatch.setattr(
        provider_routes,
        "_model_catalog_service",
        lambda: _CatalogService(),
    )

    allowed = [
        client.post(
            "/models/default/v1",
            headers=admin_auth_header,
            json={"provider_id": "local", "model_id": "model"},
        )
        for _ in range(limit)
    ]
    denied = client.post(
        "/models/default/v1",
        headers=admin_auth_header,
        json={"provider_id": "local", "model_id": "model"},
    )

    assert all(response.status_code == 200 for response in allowed)
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "43"
    assert denied.get_json()["message"] == "rate_limit_exceeded"


def test_all_kanban_writes_share_stable_rate_limit(
    app,
    client,
    admin_auth_header,
    monkeypatch,
    deterministic_surface_limiter,
) -> None:
    _enable_kanban(app)
    app.config["SURFACE_RATE_LIMITS"] = {
        KANBAN_WRITE: {"limit": 1, "window_seconds": 47}
    }
    monkeypatch.setattr(
        kanban_routes,
        "KanbanProjectionService",
        _KanbanService,
    )

    first = client.post(
        "/api/v1/kanban/boards",
        headers=admin_auth_header,
        json={"scope_type": "hub", "idempotency_key": "board-one"},
    )
    denied = client.post(
        "/api/v1/kanban/boards",
        headers=admin_auth_header,
        json={"scope_type": "hub", "idempotency_key": "board-two"},
    )

    assert first.status_code == 201
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "47"
    assert denied.get_json()["error"]["code"] == "rate_limit_exceeded"


def test_event_reconnect_has_stable_n_plus_one_limit_and_retry_after(
    app,
    client,
    admin_auth_header,
    monkeypatch,
    deterministic_surface_limiter,
) -> None:
    _enable_kanban(app)
    limit = 3
    app.config["SURFACE_RATE_LIMITS"] = {
        KANBAN_EVENT_RECONNECT: {
            "limit": limit,
            "window_seconds": 53,
        }
    }
    monkeypatch.setattr(
        kanban_routes,
        "KanbanProjectionService",
        _KanbanService,
    )
    board = client.post(
        "/api/v1/kanban/boards",
        headers=admin_auth_header,
        json={"scope_type": "hub", "idempotency_key": "event-rate-board"},
    )
    assert board.status_code == 201
    board_id = board.get_json()["data"]["id"]

    allowed = [
        client.get(
            f"/api/v1/kanban/boards/{board_id}/events",
            headers=admin_auth_header,
        )
        for _ in range(limit)
    ]
    denied = client.get(
        f"/api/v1/kanban/boards/{board_id}/events",
        headers=admin_auth_header,
    )

    assert all(response.status_code == 200 for response in allowed)
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "53"
    assert denied.get_json()["error"]["code"] == "rate_limit_exceeded"
