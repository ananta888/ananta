from __future__ import annotations

from unittest.mock import patch

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import AuditLogDB, TaskDB


class _ManagementService:
    def __init__(self) -> None:
        self.load_calls = 0
        self.configuration_updates = 0
        self.configuration_version = 1
        self.allow_cpu_fallback = False

    def status(self) -> dict:
        return {
            "status": "ready",
            "capability_catalog": [
                {
                    "schema_version": "ananta.model-capability.v1",
                    "id": "fixture/model",
                    "status": "ready",
                }
            ],
        }

    def unload(self, manifest_digest: str) -> dict:
        return {"ok": True, "manifest_digest": manifest_digest, "unloaded": True}

    def cache_gc(self) -> dict:
        return {"ok": True, "removed_entries": 3}

    def load(self, manifest_id: str, *, deadline_epoch_ms: int) -> dict:
        self.load_calls += 1
        return {
            "ok": True,
            "model": {
                "manifest_id": manifest_id,
                "manifest_digest": "b" * 64,
                "state": "idle",
            },
            "deadline_accepted": deadline_epoch_ms > 0,
            "no_generation": True,
        }

    def configuration(self) -> dict:
        return {
            "schema_version": "ananta.restricted-runtime-config.v1",
            "version": self.configuration_version,
            "mutable": {"allow_cpu_fallback": self.allow_cpu_fallback},
            "fixed": {
                "downloads_allowed": False,
                "generation_allowed": False,
                "local_snapshots_only": True,
                "trust_remote_code": False,
            },
        }

    def update_configuration(self, delta: dict, *, expected_version: int) -> dict:
        assert expected_version == self.configuration_version
        self.configuration_updates += 1
        self.allow_cpu_fallback = bool(delta["allow_cpu_fallback"])
        self.configuration_version += 1
        return {**self.configuration(), "changed": True}


def test_restricted_management_is_admin_only_and_hub_mediated(client, user_auth_header, admin_auth_header):
    service = _ManagementService()
    digest = "a" * 64
    with patch(
        "agent.routes.restricted_inference_management.get_restricted_inference_management_service",
        return_value=service,
    ):
        denied = client.get(
            "/v1/voice/restricted-inference/status",
            headers=user_auth_header,
        )
        status = client.get(
            "/v1/voice/restricted-inference/status",
            headers=admin_auth_header,
        )
        unloaded = client.post(
            f"/v1/voice/restricted-inference/models/{digest}/unload",
            headers=admin_auth_header,
        )
        gc = client.post(
            "/v1/voice/restricted-inference/cache/gc",
            headers=admin_auth_header,
        )

    assert denied.status_code == 403
    assert denied.get_json()["data"]["error"]["code"] == "voice_operation_admin_required"
    assert status.status_code == 200
    assert status.get_json()["data"]["restricted_inference"]["capability_catalog"][0]["status"] == "ready"
    assert unloaded.status_code == 200
    assert unloaded.get_json()["data"]["restricted_inference"]["manifest_digest"] == digest
    assert gc.get_json()["data"]["restricted_inference"]["removed_entries"] == 3


def test_production_model_download_endpoint_is_explicitly_fail_closed(client, admin_auth_header):
    response = client.post(
        "/v1/voice/restricted-inference/models/download",
        headers=admin_auth_header,
        json={"model": "mutable/latest"},
    )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == "offline_download_forbidden"


def test_load_and_runtime_configuration_are_hub_queued_etag_guarded_and_idempotent(
    client,
    user_auth_header,
    admin_auth_header,
):
    service = _ManagementService()
    with patch(
        "agent.routes.restricted_inference_management.get_restricted_inference_management_service",
        return_value=service,
    ):
        denied_config = client.get(
            "/v1/voice/restricted-inference/configuration",
            headers=user_auth_header,
        )
        denied_load = client.post(
            "/v1/voice/restricted-inference/models/fixture-classifier/load",
            headers=user_auth_header,
            json={},
        )
        configuration = client.get(
            "/v1/voice/restricted-inference/configuration",
            headers=admin_auth_header,
        )
        etag = configuration.headers["ETag"]
        missing_key = client.post(
            "/v1/voice/restricted-inference/models/fixture-classifier/load",
            headers=admin_auth_header,
            json={},
        )
        load_headers = {**admin_auth_header, "Idempotency-Key": "restricted-load-idempotency"}
        loaded = client.post(
            "/v1/voice/restricted-inference/models/fixture-classifier/load",
            headers=load_headers,
            json={"deadline_seconds": 30},
        )
        load_replay = client.post(
            "/v1/voice/restricted-inference/models/fixture-classifier/load",
            headers=load_headers,
            json={"deadline_seconds": 30},
        )
        stale = client.patch(
            "/v1/voice/restricted-inference/configuration",
            headers={
                **admin_auth_header,
                "Idempotency-Key": "restricted-config-stale",
                "If-Match": '"stale"',
            },
            json={"delta": {"allow_cpu_fallback": True}},
        )
        update_headers = {
            **admin_auth_header,
            "Idempotency-Key": "restricted-config-update",
            "If-Match": etag,
        }
        updated = client.patch(
            "/v1/voice/restricted-inference/configuration",
            headers=update_headers,
            json={"delta": {"allow_cpu_fallback": True}},
        )
        update_replay = client.patch(
            "/v1/voice/restricted-inference/configuration",
            headers=update_headers,
            json={"delta": {"allow_cpu_fallback": True}},
        )

    assert denied_config.status_code == 403
    assert denied_load.status_code == 403
    assert configuration.status_code == 200
    assert configuration.headers["Cache-Control"] == "no-store"
    assert configuration.get_json()["data"]["restricted_inference"]["fixed"]["downloads_allowed"] is False
    assert missing_key.status_code == 400
    assert loaded.status_code == 200
    loaded_payload = loaded.get_json()["data"]["restricted_inference"]
    assert loaded_payload["model"]["state"] == "idle"
    assert loaded_payload["management_task_id"].startswith("restricted-management-")
    assert load_replay.get_json()["data"]["restricted_inference"]["idempotent_replay"] is True
    assert service.load_calls == 1
    assert stale.status_code == 412
    assert updated.status_code == 200
    assert updated.headers["ETag"] != etag
    assert update_replay.status_code == 200
    assert update_replay.headers["ETag"] == updated.headers["ETag"]
    assert update_replay.get_json()["data"]["restricted_inference"]["idempotent_replay"] is True
    assert service.configuration_updates == 1

    with Session(engine) as session:
        task = session.get(TaskDB, loaded_payload["management_task_id"])
        audit_actions = {
            item.action
            for item in session.exec(
                select(AuditLogDB).where(
                    AuditLogDB.action.in_(
                        {
                            "restricted_inference_configuration_read",
                            "restricted_inference_configuration_updated",
                            "restricted_inference_model_loaded",
                        }
                    )
                )
            ).all()
        }
    assert task is not None
    assert task.status == "completed"
    assert task.task_kind == "restricted_inference_management"
    assert task.worker_execution_context["restricted_inference_management"]["no_generation"] is True
    assert audit_actions == {
        "restricted_inference_configuration_read",
        "restricted_inference_configuration_updated",
        "restricted_inference_model_loaded",
    }


def test_legacy_config_graph_status_is_authenticated_worker_backed_compatibility_view(
    client,
    user_auth_header,
    admin_auth_header,
):
    service = _ManagementService()
    with patch(
        "agent.services.restricted_inference_management_service.get_restricted_inference_management_service",
        return_value=service,
    ):
        denied = client.get(
            "/api/config-graph/restricted-inference/status",
            headers=user_auth_header,
        )
        allowed = client.get(
            "/api/config-graph/restricted-inference/status",
            headers=admin_auth_header,
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json()["source"] == "isolated_restricted_inference_worker"
    assert allowed.get_json()["models"][0]["id"] == "fixture/model"


def test_legacy_reload_remains_an_admin_only_additive_no_op(client, user_auth_header, admin_auth_header):
    denied = client.post(
        "/api/config-graph/restricted-inference/reload",
        headers=user_auth_header,
    )
    allowed = client.post(
        "/api/config-graph/restricted-inference/reload",
        headers=admin_auth_header,
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json() == {
        "deprecated": True,
        "message": "Restricted inference Hub management is stateless; no local service reset is required.",
        "no_op": True,
        "ok": True,
        "replacement": "/v1/voice/restricted-inference/models/{manifest_id}/load",
    }
