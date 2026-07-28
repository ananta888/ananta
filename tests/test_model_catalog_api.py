from __future__ import annotations

from agent.routes.config import providers
from agent.services.model_catalog_service import CatalogQuery
from ananta_contracts.model_catalog import (
    MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
    ModelAvailability,
    ModelCatalog,
    ModelHealth,
    ModelRuntime,
    ModelSummary,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enable_model_dashboard(app) -> None:
    config = dict(app.config.get("AGENT_CONFIG", {}) or {})
    config["feature_angular_model_dashboard_enabled"] = True
    app.config["AGENT_CONFIG"] = config


class _FakeCatalog:
    def __init__(self):
        self.queries: list[CatalogQuery] = []

    def versioned_catalog(self, query: CatalogQuery) -> ModelCatalog:
        self.queries.append(query)
        return ModelCatalog(
            models=(
                ModelSummary(
                    provider_id="openai",
                    runtime=ModelRuntime.CLOUD,
                    model_id="gpt-safe",
                    display_name="GPT Safe",
                    availability=ModelAvailability.AVAILABLE,
                    capabilities=("chat",),
                    health=ModelHealth.HEALTHY,
                    is_default=True,
                ),
            )
        )


def test_feature_contract_defaults_false_and_rejects_string_updates(
    client,
    admin_token,
):
    response = client.get(
        "/config/features/v1",
        headers=_headers(admin_token),
    )

    assert response.status_code == 200
    assert response.json["data"]["schema"] == (
        "ananta.dashboard-feature-flags.v1"
    )
    assert response.json["data"]["features"] == {
        "angular_kanban": False,
        "angular_model_dashboard": False,
        "tui_kanban": False,
        "tui_model_menu": False,
    }

    rejected = client.post(
        "/config",
        json={"feature_angular_model_dashboard_enabled": "true"},
        headers=_headers(admin_token),
    )
    assert rejected.status_code == 400
    assert (
        rejected.json["message"]
        == "invalid_feature_angular_model_dashboard_enabled"
    )


def test_versioned_catalog_is_fail_closed_until_feature_enabled(
    client,
    admin_token,
):
    response = client.get(
        "/models/catalog/v1",
        headers=_headers(admin_token),
    )

    assert response.status_code == 404
    assert response.json["message"] == "model_catalog_feature_disabled"


def test_model_catalog_uses_runtime_feature_flag_default(app, monkeypatch):
    app.config["AGENT_CONFIG"] = {}
    monkeypatch.setattr(
        providers.runtime_settings,
        "feature_angular_model_dashboard_enabled",
        True,
    )

    with app.app_context():
        assert providers._model_catalog_feature_enabled() is True


def test_versioned_catalog_and_refresh_use_safe_dedicated_contract(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    fake = _FakeCatalog()
    monkeypatch.setattr(
        "agent.routes.config.providers._model_catalog_service",
        lambda: fake,
    )

    read = client.get(
        "/models/catalog/v1",
        headers=_headers(admin_token),
    )
    refreshed = client.post(
        "/models/catalog/v1/refresh",
        headers=_headers(admin_token),
    )

    assert read.status_code == refreshed.status_code == 200
    assert read.json["data"]["schema"] == "ananta.model-catalog.v1"
    assert set(read.json["data"]["models"][0]) == {
        "schema",
        "provider_id",
        "runtime",
        "model_id",
        "display_name",
        "availability",
        "loaded",
        "context_window",
        "quantization",
        "capabilities",
        "health",
        "is_default",
    }
    assert fake.queries[0].force_refresh is False
    assert fake.queries[1].force_refresh is True


def test_refresh_and_default_selection_require_capability(
    client,
    app,
    user_auth_header,
):
    _enable_model_dashboard(app)

    refresh = client.post(
        "/models/catalog/v1/refresh",
        headers=user_auth_header,
    )
    selection = client.post(
        "/models/default/v1",
        json={
            "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            "provider_id": "openai",
            "model_id": "gpt-safe",
        },
        headers=user_auth_header,
    )

    assert refresh.status_code == selection.status_code == 403
    assert (
        refresh.json["data"]["reason_code"]
        == "model_catalog_capability_required"
    )


def test_default_selection_command_rejects_urls_paths_and_shell_fields(
    client,
    app,
    admin_token,
):
    _enable_model_dashboard(app)

    response = client.post(
        "/models/default/v1",
        json={
            "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            "provider_id": "openai",
            "model_id": "gpt-safe",
            "base_url": "https://attacker.invalid",
            "path": "../../etc/passwd",
            "shell_args": ["rm", "-rf"],
        },
        headers=_headers(admin_token),
    )

    assert response.status_code == 400
    assert (
        response.json["message"]
        == "model_default_selection_command_invalid"
    )
