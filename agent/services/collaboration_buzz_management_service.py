"""Hub-admin lifecycle and secret-safe persistence for optional Buzz bridges."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.services.collaboration_bridge_ports import BuzzBridgeConfig
from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id


class BuzzBridgeLifecyclePort(Protocol):
    @property
    def capabilities(self) -> Mapping[str, Any]: ...

    def connect(self) -> Mapping[str, Any]: ...

    def disconnect(self) -> Mapping[str, Any]: ...


class BuzzBridgeFactory(Protocol):
    def create(self, config: BuzzBridgeConfig) -> BuzzBridgeLifecyclePort: ...


class UnavailableBuzzBridgeFactory:
    def create(self, config: BuzzBridgeConfig) -> BuzzBridgeLifecyclePort:
        del config
        raise RuntimeError("buzz_runtime_provider_not_configured")


class BuzzBridgeConfigurationStore:
    """Small CAS repository; secret material is never accepted, only references."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_buzz_configurations(
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, workspace_id)
                )
                """
            )

    def get(self, tenant_id: str, workspace_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_buzz_configurations WHERE tenant_id=? AND workspace_id=?",
                (require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(
        self,
        tenant_id: str,
        workspace_id: str,
        configuration: Mapping[str, Any],
        *,
        state: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        if state not in {"disabled", "approved", "connected", "disconnected", "blocked"}:
            raise ValueError("buzz_bridge_lifecycle_state_invalid")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("buzz_bridge_configuration_revision_invalid")
        with sqlite3.connect(self._path, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM collaboration_buzz_configurations WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchone()
            actual = int(row[0]) if row else 0
            if actual != expected_revision:
                raise CollaborationStoreConflict("buzz_bridge_configuration_revision_conflict")
            value = {
                **dict(configuration),
                "tenant_id": tenant,
                "workspace_id": workspace,
                "state": state,
                "revision": actual + 1,
            }
            value["config_digest"] = canonical_digest(_configuration_digest_input(value))
            connection.execute(
                "INSERT INTO collaboration_buzz_configurations(tenant_id,workspace_id,revision,state,"
                "config_digest,payload_json) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET "
                "revision=excluded.revision,state=excluded.state,config_digest=excluded.config_digest,"
                "payload_json=excluded.payload_json",
                (tenant, workspace, value["revision"], state, value["config_digest"], canonical_json(value)),
            )
            connection.commit()
        return value


class CollaborationBuzzManagementService:
    CONFIGURATION_FIELDS = frozenset(
        {
            "adapter_id",
            "relay_host",
            "community_id",
            "tls_required",
            "auth_ref",
            "signing_key_ref",
            "enabled",
        }
    )

    def __init__(
        self,
        store: BuzzBridgeConfigurationStore,
        *,
        workspaces: CollaborationWorkspaceStore,
        factory: BuzzBridgeFactory,
    ) -> None:
        self._store = store
        self._workspaces = workspaces
        self._factory = factory
        self._active: dict[tuple[str, str], BuzzBridgeLifecyclePort] = {}

    def configure(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        configuration: Mapping[str, Any],
        expected_revision: int,
        admin_authorized: bool,
    ) -> dict[str, Any]:
        self._require_admin(admin_authorized)
        self._workspaces.get_workspace(tenant_id, workspace_id)
        normalized = self._normalize(configuration)
        current = self._store.get(tenant_id, workspace_id)
        if current is not None and current["state"] == "connected":
            raise CollaborationStoreConflict("buzz_bridge_disconnect_required")
        state = "approved" if normalized["enabled"] else "disabled"
        value = self._store.put(
            tenant_id,
            workspace_id,
            normalized,
            state=state,
            expected_revision=expected_revision,
        )
        return self._projection(
            value, "buzz_bridge_configuration_approved" if normalized["enabled"] else "buzz_bridge_disabled"
        )

    def connect(
        self, *, tenant_id: str, workspace_id: str, expected_revision: int, admin_authorized: bool
    ) -> dict[str, Any]:
        self._require_admin(admin_authorized)
        current = self._required(tenant_id, workspace_id)
        if current["revision"] != expected_revision:
            raise CollaborationStoreConflict("buzz_bridge_configuration_revision_conflict")
        if not current["enabled"]:
            return self._projection(current, "buzz_bridge_disabled")
        config = self._bridge_config(current)
        try:
            bridge = self._factory.create(config)
            result = dict(bridge.connect())
        except RuntimeError as exc:
            reason = str(exc)
            if reason != "buzz_runtime_provider_not_configured":
                reason = "buzz_bridge_connection_failed"
            return self._transition(current, state="blocked", reason_code=reason)
        except Exception:
            return self._transition(current, state="blocked", reason_code="buzz_bridge_connection_failed")
        if result.get("connected") is not True:
            return self._transition(
                current,
                state="blocked",
                reason_code=_safe_reason(result.get("reason_code"), "buzz_bridge_connection_failed"),
            )
        self._active[(tenant_id, workspace_id)] = bridge
        return self._transition(current, state="connected", reason_code="buzz_bridge_connected")

    def disconnect(
        self, *, tenant_id: str, workspace_id: str, expected_revision: int, admin_authorized: bool
    ) -> dict[str, Any]:
        self._require_admin(admin_authorized)
        current = self._required(tenant_id, workspace_id)
        if current["revision"] != expected_revision:
            raise CollaborationStoreConflict("buzz_bridge_configuration_revision_conflict")
        bridge = self._active.pop((tenant_id, workspace_id), None)
        if bridge is not None:
            try:
                bridge.disconnect()
            except Exception:
                return self._transition(
                    current,
                    state="blocked",
                    reason_code="buzz_bridge_disconnect_failed",
                )
        return self._transition(current, state="disconnected", reason_code="buzz_bridge_disconnected")

    def status(self, *, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        self._workspaces.get_workspace(tenant_id, workspace_id)
        current = self._store.get(tenant_id, workspace_id)
        if current is None:
            return {
                "schema": "ananta.buzz-bridge-lifecycle.v1",
                "workspace_id": workspace_id,
                "state": "disabled",
                "revision": 0,
                "reason_code": "buzz_bridge_not_configured",
                "native_core_available": True,
                "auth_configured": False,
                "signing_key_configured": False,
            }
        if current["state"] == "connected" and (tenant_id, workspace_id) not in self._active:
            return {
                **self._projection(current, "buzz_bridge_process_session_unavailable"),
                "state": "disconnected",
            }
        return self._projection(current, _state_reason(current["state"]))

    def active_bridge(self, *, tenant_id: str, workspace_id: str) -> BuzzBridgeLifecyclePort | None:
        return self._active.get((tenant_id, workspace_id))

    def _transition(self, current: Mapping[str, Any], *, state: str, reason_code: str) -> dict[str, Any]:
        value = self._store.put(
            current["tenant_id"],
            current["workspace_id"],
            {key: current[key] for key in self.CONFIGURATION_FIELDS},
            state=state,
            expected_revision=int(current["revision"]),
        )
        return self._projection(value, reason_code)

    def _required(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        self._workspaces.get_workspace(tenant_id, workspace_id)
        value = self._store.get(tenant_id, workspace_id)
        if value is None:
            raise KeyError("buzz_bridge_configuration_not_found")
        return value

    @classmethod
    def _normalize(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        if set(value) != cls.CONFIGURATION_FIELDS:
            raise ValueError("buzz_bridge_configuration_fields_invalid")
        enabled = value.get("enabled")
        tls_required = value.get("tls_required")
        if not isinstance(enabled, bool) or not isinstance(tls_required, bool):
            raise ValueError("buzz_bridge_configuration_invalid")
        normalized = {
            "adapter_id": require_id(value.get("adapter_id"), "adapter_id"),
            "relay_host": str(value.get("relay_host") or "").strip(),
            "community_id": require_id(value.get("community_id"), "community_id"),
            "tls_required": tls_required,
            "auth_ref": require_id(value.get("auth_ref"), "auth_ref") if value.get("auth_ref") else None,
            "signing_key_ref": require_id(value.get("signing_key_ref"), "signing_key_ref")
            if value.get("signing_key_ref")
            else None,
            "enabled": enabled,
        }
        BuzzBridgeConfig(
            "validation-tenant",
            "validation-workspace",
            normalized["adapter_id"],
            normalized["relay_host"],
            enabled=enabled,
            tls_required=tls_required,
            community_id=normalized["community_id"],
            auth_ref=normalized["auth_ref"],
            signing_key_ref=normalized["signing_key_ref"],
        )
        if enabled and (not tls_required or not normalized["auth_ref"] or not normalized["signing_key_ref"]):
            raise ValueError("buzz_bridge_secure_configuration_required")
        return normalized

    @staticmethod
    def _bridge_config(value: Mapping[str, Any]) -> BuzzBridgeConfig:
        return BuzzBridgeConfig(
            value["tenant_id"],
            value["workspace_id"],
            value["adapter_id"],
            value["relay_host"],
            enabled=value["enabled"],
            tls_required=value["tls_required"],
            community_id=value["community_id"],
            auth_ref=value["auth_ref"],
            signing_key_ref=value["signing_key_ref"],
        )

    @staticmethod
    def _projection(value: Mapping[str, Any], reason_code: str) -> dict[str, Any]:
        return {
            "schema": "ananta.buzz-bridge-lifecycle.v1",
            "workspace_id": value["workspace_id"],
            "adapter_id": value["adapter_id"],
            "relay_host": value["relay_host"],
            "community_id": value["community_id"],
            "tls_required": value["tls_required"],
            "enabled": value["enabled"],
            "state": value["state"],
            "revision": value["revision"],
            "config_digest": value["config_digest"],
            "reason_code": reason_code,
            "native_core_available": True,
            "auth_configured": value.get("auth_ref") is not None,
            "signing_key_configured": value.get("signing_key_ref") is not None,
        }

    @staticmethod
    def _require_admin(admin_authorized: bool) -> None:
        if admin_authorized is not True:
            raise PermissionError("buzz_bridge_hub_admin_required")


def _configuration_digest_input(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in CollaborationBuzzManagementService.CONFIGURATION_FIELDS}


def _safe_reason(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    try:
        return require_id(text, "reason_code")
    except ValueError:
        return fallback


def _state_reason(state: str) -> str:
    return {
        "disabled": "buzz_bridge_disabled",
        "approved": "buzz_bridge_configuration_approved",
        "connected": "buzz_bridge_connected",
        "disconnected": "buzz_bridge_disconnected",
        "blocked": "buzz_bridge_connection_blocked",
    }[state]


__all__ = [
    "BuzzBridgeConfigurationStore",
    "BuzzBridgeFactory",
    "BuzzBridgeLifecyclePort",
    "CollaborationBuzzManagementService",
    "UnavailableBuzzBridgeFactory",
]
