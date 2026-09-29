from __future__ import annotations

from agent.auth import generate_token
from agent.config import settings
from agent.routes.hrm_experiments import HRM_CONTROL_PLANE_EXTENSION
from agent.services.hrm_experiments import (
    HrmExperimentControlPlaneService,
    HrmWorkerCapability,
    default_hrm_contract_validator,
)


def _headers() -> dict[str, str]:
    token = generate_token(
        {"sub": "hrm-operator", "role": "user", "tenant_id": "tenant-a"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


class _StaticCapabilityPort:
    def read_capability(self) -> HrmWorkerCapability:
        return HrmWorkerCapability(
            worker_id="worker-hrm-1",
            runtime={
                "engine_version": "hrm-engine-v1",
                "image_digest": "a" * 64,
                "python_version": "3.12.4",
                "torch_version": "2.4.0",
                "cuda_version": None,
                "flash_attention_version": None,
            },
            device={"kind": "cpu", "device_ids": [], "vram_bytes": 0},
            isolation={
                "profile_version": "hrm-isolation-v1",
                "non_root": True,
                "no_new_privileges": True,
                "cap_drop_all": True,
                "read_only_rootfs": True,
                "network_denied": True,
                "cgroup_limits": True,
                "seccomp": True,
                "mac_policy": True,
            },
            supported_profiles=("hrm-safe-cpu-v1",),
        )


def _install(client, *, enabled: bool, worker: bool = False) -> None:
    client.application.extensions[HRM_CONTROL_PLANE_EXTENSION] = (
        HrmExperimentControlPlaneService(
            feature_enabled=enabled,
            worker_capabilities=_StaticCapabilityPort() if worker else None,
        )
    )


def test_hrm_capability_requires_auth_and_is_closed_default_deny(client):
    _install(client, enabled=False)

    assert client.get("/api/hrm-experiments/capabilities").status_code == 401
    response = client.get(
        "/api/hrm-experiments/capabilities",
        headers=_headers(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.hrm-experiments.capability.v1"
    assert payload["feature_enabled"] is False
    assert payload["worker_id"] == "unavailable"
    assert payload["supported_profiles"] == []
    default_hrm_contract_validator.validate("capability_probe", payload)


def test_hrm_capability_rejects_unknown_query_fields(client):
    _install(client, enabled=False)

    response = client.get(
        "/api/hrm-experiments/capabilities?worker_id=worker-hrm-1",
        headers=_headers(),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "status": "error",
        "reason_code": "hrm.capability_query_forbidden",
    }


def test_hrm_preflight_is_non_dispatching_and_denies_disabled_feature(client):
    _install(client, enabled=False)

    response = client.post(
        "/api/hrm-experiments/preflight",
        headers=_headers(),
        json={"project_id": "project-a", "profile_id": "hrm-safe-cpu-v1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["allowed"] is False
    assert payload["reason_codes"] == ["hrm.feature_disabled"]
    default_hrm_contract_validator.validate("preflight_result", payload)


def test_hrm_preflight_allows_only_supported_isolated_worker_profile(client):
    _install(client, enabled=True, worker=True)

    response = client.post(
        "/api/hrm-experiments/preflight",
        headers=_headers(),
        json={"project_id": "project-a", "profile_id": "hrm-safe-cpu-v1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["allowed"] is True
    assert payload["reason_codes"] == []
    assert len(payload["capability_digest"]) == 64
    assert len(payload["policy_digest"]) == 64
    default_hrm_contract_validator.validate("preflight_result", payload)


def test_hrm_preflight_rejects_open_or_malformed_requests(client):
    _install(client, enabled=True, worker=True)

    extra = client.post(
        "/api/hrm-experiments/preflight",
        headers=_headers(),
        json={
            "project_id": "project-a",
            "profile_id": "hrm-safe-cpu-v1",
            "dispatch": True,
        },
    )
    malformed = client.post(
        "/api/hrm-experiments/preflight",
        headers=_headers(),
        json={"project_id": "../project-a", "profile_id": "hrm-safe-cpu-v1"},
    )

    assert extra.status_code == 400
    assert malformed.status_code == 400
    assert extra.get_json()["reason_code"] == "hrm.preflight_request_invalid"
    assert malformed.get_json()["reason_code"] == "hrm.preflight_request_invalid"
