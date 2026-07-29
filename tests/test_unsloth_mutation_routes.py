from __future__ import annotations

import uuid
from pathlib import Path

from agent.auth import generate_token
from agent.config import settings
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_mutation_command_service import (
    UnslothMutationExecutor,
    project_unsloth_capabilities,
)


class _ExportExecutor:
    def __init__(self) -> None:
        self.preview_calls = 0
        self.execute_calls = 0

    def preview(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
    ):
        self.preview_calls += 1
        return {
            "adapter_id": resource_id,
            "tenant_scope": principal.tenant_id,
            "reason_length": len(reason),
        }

    def execute(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ):
        self.execute_calls += 1
        return {
            "artifact_id": f"lora-export-{resource_id}",
            "tenant_scope": principal.tenant_id,
            "reason_length": len(reason),
            "task_key_digest": idempotency_key[:8],
            "download_url": f"/api/ml-intern-training/exports/lora-export-{resource_id}",
        }


def _configure(app, tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex
    artifact_root = tmp_path / f"artifacts-{suffix}"
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG") or {}),
        "ml_intern_training": {
            "enabled": True,
            "mode": "dry_run",
            "backend": "mock",
            "artifact_root": str(artifact_root),
            "dataset_root": str(tmp_path / f"datasets-{suffix}"),
            "base_models": ["local/base"],
        },
        "lora_runtime": {
            "adapter_registry_path": str(
                artifact_root / "adapter_registry.json"
            ),
        },
    }


def _with_key(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def _tenant_admin_headers(tenant_id: str, subject: str) -> dict[str, str]:
    token = generate_token(
        {
            "sub": subject,
            "username": subject,
            "tenant_id": tenant_id,
            "role": "admin",
        },
        settings.secret_key,
        expires_in=3600,
    )
    return {"Authorization": f"Bearer {token}"}


def _body(**overrides):
    body = {
        "operation": "export",
        "resource_id": "adapter_01JABC",
        "reason": "Publish the reviewed adapter",
        "dry_run": True,
        "confirmed": False,
    }
    body.update(overrides)
    return body


def test_capabilities_expose_fail_closed_unsloth_projection(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    executor = _ExportExecutor()
    assert isinstance(executor, UnslothMutationExecutor)
    app.extensions["unsloth_export_mutation_executor"] = executor

    response = client.get(
        "/api/ml-intern-training/capabilities",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert "unsloth_capabilities" in payload
    projection = payload["unsloth"]
    assert projection["operations"]["export"]["available"] is True
    assert projection["operations"]["runtime_handoff"] == {
        "available": True,
        "reason_code": None,
        "version": "ananta.runtime-handoff.v2",
    }
    assert projection["operations"]["mcp"]["available"] is False
    assert projection["release_profile"]["available"] is False
    assert projection["release_profile"]["reason_code"] == (
        "unsloth_release_profile_unavailable"
    )
    assert set(projection["modalities"]) == {
        "text",
        "vision",
        "audio",
        "embedding",
    }


def test_mutation_route_requires_admin_auth_and_idempotency(
    app,
    client,
    admin_auth_header,
    user_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    app.extensions["unsloth_export_mutation_executor"] = _ExportExecutor()
    route = "/api/ml-intern-training/unsloth/mutations/export"

    assert client.post(route, json=_body()).status_code == 401
    assert (
        client.post(
            route,
            headers=_with_key(user_auth_header, "unsloth-user-denied-001"),
            json=_body(),
        ).status_code
        == 403
    )
    missing = client.post(route, headers=admin_auth_header, json=_body())
    assert missing.status_code == 400
    assert missing.get_json()["data"]["error"]["code"] == (
        "idempotency_key_invalid"
    )


def test_dry_run_confirmation_is_tenant_bound_and_idempotent(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.routes import ml_intern_training as training_routes

    _configure(app, tmp_path)
    executor = _ExportExecutor()
    app.extensions["unsloth_export_mutation_executor"] = executor
    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        training_routes,
        "log_audit",
        lambda event_type, details: audit_events.append(
            (event_type, dict(details))
        ),
    )
    route = "/api/ml-intern-training/unsloth/mutations/export"

    invalid_resource = client.post(
        route,
        headers=_with_key(admin_auth_header, "unsloth-invalid-path-001"),
        json=_body(resource_id="/host/models/private"),
    )
    assert invalid_resource.status_code == 422
    assert invalid_resource.get_json()["data"]["error"]["code"] == (
        "unsloth_resource_id_invalid"
    )
    assert executor.preview_calls == 0
    assert executor.execute_calls == 0

    dry_run = client.post(
        route,
        headers=_with_key(admin_auth_header, "unsloth-dry-run-001"),
        json=_body(),
    )
    assert dry_run.status_code == 200
    dry_result = dry_run.get_json()["data"]
    confirmation_id = dry_result["confirmation_id"]
    assert dry_result["reason_code"] == "unsloth_mutation_dry_run_ready"
    assert executor.preview_calls == 1
    assert executor.execute_calls == 0

    changed = client.post(
        route,
        headers=_with_key(admin_auth_header, "unsloth-confirm-changed-001"),
        json=_body(
            reason="Publish a materially different adapter request",
            dry_run=False,
            confirmed=True,
            confirmation_id=confirmation_id,
        ),
    )
    assert changed.status_code == 409
    assert changed.get_json()["data"]["error"]["code"] == (
        "unsloth_confirmation_invalid"
    )
    assert executor.execute_calls == 0

    other_tenant = client.post(
        route,
        headers=_with_key(
            _tenant_admin_headers("tenant-b", "tenant-b-admin"),
            "unsloth-confirm-tenant-b-001",
        ),
        json=_body(
            dry_run=False,
            confirmed=True,
            confirmation_id=confirmation_id,
        ),
    )
    assert other_tenant.status_code == 409
    assert other_tenant.get_json()["data"]["error"]["code"] == (
        "unsloth_confirmation_invalid"
    )
    assert executor.execute_calls == 0

    confirm_headers = _with_key(
        admin_auth_header,
        "unsloth-confirm-export-001",
    )
    confirm_body = _body(
        dry_run=False,
        confirmed=True,
        confirmation_id=confirmation_id,
    )
    confirmed = client.post(route, headers=confirm_headers, json=confirm_body)
    replayed = client.post(route, headers=confirm_headers, json=confirm_body)

    assert confirmed.status_code == 201
    assert confirmed.get_json()["data"]["reason_code"] == (
        "unsloth_mutation_completed"
    )
    assert replayed.status_code == 200
    assert replayed.get_json()["data"]["replayed"] is True
    assert executor.execute_calls == 1
    assert all(event == "ml_intern_unsloth_mutation" for event, _ in audit_events)
    assert {details["outcome"] for _, details in audit_events} == {
        "accepted",
        "denied",
    }


def test_incomplete_runtime_handoff_and_invalid_snapshots_fail_closed(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    projection = project_unsloth_capabilities(
        {"facets": [{"id": "training.text", "available": True, "reason_code": "invalid"}]},
        executable_operations=(),
    )
    assert projection["core"]["available"] is False
    assert projection["core"]["reason_code"] == (
        "unsloth_capability_contract_invalid"
    )

    response = client.post(
        "/api/ml-intern-training/unsloth/mutations/runtime_handoff",
        headers=_with_key(admin_auth_header, "unsloth-runtime-closed-001"),
        json=_body(operation="runtime_handoff"),
    )
    assert response.status_code == 422
    assert response.get_json()["data"]["error"]["code"] == (
        "runtime_handoff_artifact_id_invalid"
    )
