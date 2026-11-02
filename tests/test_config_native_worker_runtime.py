"""Configuration round trips must preserve the selected worker execution mode."""

from types import SimpleNamespace

import pytest

from agent.routes.config.config_update_contract import ConfigUpdateRejected
from agent.routes.config.config_update_runtime_normalizers import normalize_worker_runtime


def test_api_can_switch_native_runtime_off_and_back_on(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    for enabled in (False, True):
        response = client.post(
            "/config",
            json={"worker_runtime": {"native_worker_runtime": {"enabled": enabled}}},
            headers=headers,
        )
        assert response.status_code == 200
        current = client.get("/config", headers=headers).get_json()["data"]
        assert current["worker_runtime"]["native_worker_runtime"]["enabled"] is enabled


@pytest.mark.parametrize("enabled", [True, False])
def test_native_runtime_mode_survives_config_normalization(enabled):
    result = normalize_worker_runtime(
        {"worker_runtime": {"native_worker_runtime": {"enabled": enabled}}},
        SimpleNamespace(current_cfg={}),
    )
    assert result["worker_runtime"]["native_worker_runtime"] == {
        "enabled": enabled,
        "fallback_backend": "sgpt",
    }


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_native_runtime_rejects_non_boolean_mode(value):
    with pytest.raises(ConfigUpdateRejected, match="invalid_native_worker_runtime_enabled"):
        normalize_worker_runtime(
            {"worker_runtime": {"native_worker_runtime": {"enabled": value}}},
            SimpleNamespace(current_cfg={}),
        )


def test_native_runtime_rejects_unknown_fallback():
    with pytest.raises(ConfigUpdateRejected, match="invalid_native_worker_runtime_fallback_backend"):
        normalize_worker_runtime(
            {"worker_runtime": {"native_worker_runtime": {"fallback_backend": "arbitrary"}}},
            SimpleNamespace(current_cfg={}),
        )


def test_unrelated_runtime_update_does_not_add_native_mode():
    result = normalize_worker_runtime(
        {"worker_runtime": {"workspace_root": "/workspaces"}},
        SimpleNamespace(current_cfg={}),
    )
    assert "native_worker_runtime" not in result["worker_runtime"]
