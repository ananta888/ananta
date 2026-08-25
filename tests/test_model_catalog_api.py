from __future__ import annotations

from agent.routes.config import providers, settings as settings_routes
from agent.services.model_catalog_service import CatalogQuery
from agent.services.model_routing_transfer_service import (
    ModelRoutingTransferService,
)
from agent.services.model_routing_template_service import (
    ModelRoutingTemplateService,
)
from agent.services.model_routing_legacy_migration_service import (
    ModelRoutingLegacyMigrationService,
)
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import (
    InMemoryModelRoutingConfigurationRepository,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
)
from ananta_contracts.model_catalog import (
    MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
    ModelAvailability,
    ModelCatalog,
    ModelCatalogV2,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelInventorySourceStatus,
    ModelRuntime,
    ModelSourceKind,
    ModelSummary,
)
from ananta_contracts.model_selection import EffectiveModelRoute, ModelRouteDecision


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enable_model_dashboard(app) -> None:
    config = dict(app.config.get("AGENT_CONFIG", {}) or {})
    config["feature_angular_model_dashboard_enabled"] = True
    config["feature_model_catalog_v2_enabled"] = True
    config["feature_model_routing_editor_enabled"] = True
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


class _FakeInventory:
    def __init__(self):
        self.force_refresh_values = []

    def catalog(self, *, force_refresh: bool = False):
        self.force_refresh_values.append(force_refresh)
        return ModelCatalogV2(
            catalog_revision=2,
            models=(ModelInventoryDescriptor(
                provider_id="codex",
                model_id="gpt-safe",
                executor_id="cli:codex",
                display_name="Codex",
                runtime=ModelRuntime.CLOUD,
                source_ids=("cli:codex",),
                source_kinds=(ModelSourceKind.CONFIGURED,),
                availability=ModelAvailability.UNKNOWN,
                health=ModelHealth.UNKNOWN,
                installed=True,
                listing_supported=False,
            ),),
            sources=(ModelInventorySourceStatus(
                source_id="cli:codex",
                source_kind=ModelSourceKind.CONFIGURED,
                status="healthy",
                model_count=1,
            ),),
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
        "model_catalog_v2": False,
        "model_routing_editor": False,
        "legacy_model_picker_deprecation": False,
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


def test_routing_editor_activation_is_blocked_by_release_gate(
    client,
    app,
    admin_token,
    monkeypatch,
):
    class _Gate:
        ready = False

        @staticmethod
        def model_dump(**_kwargs):
            return {
                "schema": "ananta.model-routing-release-gate.v1",
                "configuration_revision": 0,
                "ready": False,
                "checks": [],
            }

    class _Migration:
        @staticmethod
        def release_gate():
            return _Gate()

    monkeypatch.setattr(
        settings_routes,
        "build_model_routing_legacy_migration_service",
        lambda **_kwargs: _Migration(),
    )
    app.config["AGENT_CONFIG"] = {}

    response = client.post(
        "/config",
        json={"feature_model_routing_editor_enabled": True},
        headers=_headers(admin_token),
    )

    assert response.status_code == 409
    assert response.json["message"] == "model_routing_editor_release_gate_failed"
    assert app.config["AGENT_CONFIG"].get(
        "feature_model_routing_editor_enabled"
    ) is not True


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


def test_catalog_v2_and_refresh_use_canonical_inventory_service(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    inventory = _FakeInventory()
    monkeypatch.setattr(providers, "_model_inventory_service", lambda: inventory)

    read = client.get(
        "/models/catalog/v2", headers=_headers(admin_token)
    )
    refreshed = client.post(
        "/models/catalog/v2/refresh", headers=_headers(admin_token)
    )

    assert read.status_code == refreshed.status_code == 200
    assert read.json["data"]["schema"] == "ananta.model-catalog.v2"
    assert read.json["data"]["models"][0]["executor_id"] == "cli:codex"
    assert read.json["data"]["models"][0]["listing_supported"] is False
    assert inventory.force_refresh_values == [False, True]


def test_staged_catalog_and_editor_flags_fail_closed_independently(
    client,
    app,
    admin_token,
    monkeypatch,
):
    config = dict(app.config.get("AGENT_CONFIG", {}) or {})
    config["feature_angular_model_dashboard_enabled"] = True
    config["feature_model_catalog_v2_enabled"] = False
    config["feature_model_routing_editor_enabled"] = False
    app.config["AGENT_CONFIG"] = config
    monkeypatch.setattr(providers, "_model_catalog_service", lambda: _FakeCatalog())
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local-code",),
    )
    monkeypatch.setattr(providers, "_model_routing_service", lambda: assignments)

    v1 = client.get("/models/catalog/v1", headers=_headers(admin_token))
    v2 = client.get("/models/catalog/v2", headers=_headers(admin_token))
    mutation = client.put(
        "/models/routing/v1",
        json={
            "schema": "ananta.model-routing-mutation-command.v1",
            "expected_revision": 0,
            "assignments": [],
            "fallback_groups": [],
        },
        headers=_headers(admin_token),
    )

    assert v1.status_code == 200
    assert v2.status_code == 404
    assert v2.json["message"] == "model_catalog_feature_disabled"
    assert mutation.status_code == 404
    assert mutation.json["message"] == "model_routing_editor_feature_disabled"
    assert assignments.read().revision == 0


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


class _FakeEffectiveRouting:
    def __init__(self):
        self.commands = []

    def dry_run(self, command):
        self.commands.append(command)
        return EffectiveModelRoute(
            configuration_revision=3,
            consumer_id=command.consumer_id,
            assignment_source="global",
            assignment_mode="profile",
            resolved_profile_id="local-code",
            provider_id="lmstudio",
            model_id="kat-coder",
            candidate_profile_ids=("local-code",),
            decisions=(ModelRouteDecision(
                rank=1,
                source="request_runtime_override",
                profile_id="local-code",
                accepted=True,
                reason="accepted",
            ),),
            executable=True,
        )


def test_model_routing_dry_run_has_closed_read_only_contract(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    fake = _FakeEffectiveRouting()
    monkeypatch.setattr(
        providers,
        "_effective_model_routing_service",
        lambda: fake,
    )

    response = client.post(
        "/models/routing/v1/dry-run",
        json={
            "schema": "ananta.model-routing-dry-run-command.v1",
            "consumer_id": "task.coding",
            "project_id": "ananta",
            "contains_secrets": True,
            "unknown_field": "rejected",
        },
        headers=_headers(admin_token),
    )
    valid = client.post(
        "/models/routing/v1/dry-run",
        json={
            "schema": "ananta.model-routing-dry-run-command.v1",
            "consumer_id": "task.coding",
            "project_id": "ananta",
            "contains_secrets": True,
        },
        headers=_headers(admin_token),
    )

    assert response.status_code == 400
    assert response.json["message"] == "model_routing_dry_run_command_invalid"
    assert valid.status_code == 200
    assert valid.json["data"]["schema"] == "ananta.effective-model-route.v1"
    assert valid.json["data"]["resolved_profile_id"] == "local-code"
    assert fake.commands[0].contains_secrets is True


def test_effective_model_routing_projection_covers_routable_consumers_only(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    fake = _FakeEffectiveRouting()
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local-code",),
    )
    monkeypatch.setattr(providers, "_model_routing_service", lambda: assignments)
    monkeypatch.setattr(providers, "_effective_model_routing_service", lambda: fake)

    response = client.get(
        "/models/routing/v1/effective", headers=_headers(admin_token)
    )

    assert response.status_code == 200
    assert response.json["data"]["schema"] == (
        "ananta.effective-model-routing-projection.v1"
    )
    ids = {item["consumer_id"] for item in response.json["data"]["routes"]}
    assert "task.coding" in ids
    assert "knowledge.embedding" not in ids
    assert response.json["data"]["configuration_revision"] == 0


def test_model_routing_dry_run_requires_read_capability(
    client,
    app,
    user_auth_header,
):
    _enable_model_dashboard(app)

    response = client.post(
        "/models/routing/v1/dry-run",
        json={"consumer_id": "task.coding"},
        headers=user_auth_header,
    )

    assert response.status_code == 403
    assert response.json["data"]["reason_code"] == (
        "model_catalog_capability_required"
    )


def test_model_routing_export_preview_and_confirmed_import_flow(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    transfer = ModelRoutingTransferService(ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local-code",),
    ))
    monkeypatch.setattr(
        providers,
        "_model_routing_transfer_service",
        lambda: transfer,
    )
    body = {
        "schema": "ananta.model-routing-import-command.v1",
        "expected_revision": 0,
        "configuration": {
            "schema": "ananta.model-routing-config.v1",
            "revision": 77,
            "assignments": [{
                "consumer_id": "task.coding",
                "scope": "global",
                "scope_id": "global",
                "mode": "profile",
                "profile_id": "local-code",
            }],
            "fallback_groups": [],
        },
    }

    exported = client.get(
        "/models/routing/v1/export", headers=_headers(admin_token)
    )
    previewed = client.post(
        "/models/routing/v1/import/preview",
        json=body,
        headers=_headers(admin_token),
    )
    body["confirmation_digest"] = previewed.json["data"][
        "confirmation_digest"
    ]
    applied = client.post(
        "/models/routing/v1/import/apply",
        json=body,
        headers=_headers(admin_token),
    )

    assert exported.status_code == 200
    assert exported.json["data"]["schema"] == "ananta.model-routing-export.v1"
    assert previewed.status_code == 200
    assert previewed.json["data"]["applicable"] is True
    assert applied.status_code == 200
    assert applied.json["data"]["revision"] == 1


def test_model_routing_templates_are_read_only_secret_free_drafts(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=("local-safe",),
    )
    templates = ModelRoutingTemplateService(
        consumers=ModelConsumerRegistry.defaults(),
        profiles=(ModelProfile(
            profile_id="local-safe", provider_id="lmstudio", model="safe",
            local=True, supports_tools=True, supports_json=True,
        ),),
    )
    monkeypatch.setattr(providers, "_model_routing_service", lambda: assignments)
    monkeypatch.setattr(providers, "_model_routing_template_service", lambda: templates)

    response = client.get(
        "/models/routing/v1/templates", headers=_headers(admin_token)
    )

    assert response.status_code == 200
    assert response.json["data"]["schema"] == (
        "ananta.model-routing-template-catalog.v1"
    )
    assert response.json["data"]["configuration_revision"] == 0
    assert len(response.json["data"]["templates"]) == 4
    assert "base_url" not in response.text
    assert "api_key" not in response.text


def test_legacy_migration_preview_apply_shadow_and_release_gate(
    client,
    app,
    admin_token,
    monkeypatch,
):
    _enable_model_dashboard(app)
    profile = ModelProfile(
        profile_id="local-chat", provider_id="lmstudio", model="lfm2.5",
        local=True,
    )
    assignments = ModelRoutingAssignmentService(
        repository=InMemoryModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        known_profile_ids=(profile.profile_id,),
        known_models=((profile.provider_id, profile.model),),
    )
    migration = ModelRoutingLegacyMigrationService(
        assignments=assignments,
        profiles=(profile,),
        legacy_config={
            "default_provider": "lmstudio", "default_model": "lfm2.5",
        },
    )
    monkeypatch.setattr(
        providers, "_model_routing_legacy_migration_service", lambda: migration
    )

    preview = client.get(
        "/models/routing/v1/migration/preview", headers=_headers(admin_token)
    )
    applied = client.post(
        "/models/routing/v1/migration/apply",
        json={
            "schema": "ananta.model-routing-legacy-migration-apply-command.v1",
            "expected_revision": preview.json["data"]["current_revision"],
            "confirmation_digest": preview.json["data"]["confirmation_digest"],
        },
        headers=_headers(admin_token),
    )
    shadow = client.get(
        "/models/routing/v1/shadow", headers=_headers(admin_token)
    )
    gate = client.get(
        "/models/routing/v1/release-gate", headers=_headers(admin_token)
    )

    assert preview.status_code == applied.status_code == 200
    assert preview.json["data"]["applicable"] is True
    assert applied.json["data"]["revision"] == 1
    assert shadow.json["data"]["matches"] is True
    assert gate.json["data"]["ready"] is True


def test_legacy_migration_apply_requires_mutation_capability(
    client,
    app,
    user_auth_header,
):
    _enable_model_dashboard(app)
    response = client.post(
        "/models/routing/v1/migration/apply",
        json={},
        headers=user_auth_header,
    )
    assert response.status_code == 403


def test_model_routing_transfer_capabilities_are_separated(
    client,
    app,
    user_auth_header,
):
    _enable_model_dashboard(app)
    exported = client.get(
        "/models/routing/v1/export", headers=user_auth_header
    )
    previewed = client.post(
        "/models/routing/v1/import/preview",
        json={},
        headers=user_auth_header,
    )
    applied = client.post(
        "/models/routing/v1/import/apply",
        json={},
        headers=user_auth_header,
    )

    assert exported.status_code == previewed.status_code == applied.status_code == 403
