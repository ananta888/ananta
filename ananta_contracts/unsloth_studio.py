"""Pinned response contracts for the optional Unsloth Studio integration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

UNSLOTH_STUDIO_UPSTREAM_CONTRACT_VERSION = "unsloth.studio.api.2026-07.v1"
UNSLOTH_STUDIO_PROBE_SCHEMA_VERSION = "ananta.unsloth-studio-probe.v1"
UNSLOTH_MCP_PROTOCOL_VERSION = "2025-06-18"

_TOKEN_FIELDS = frozenset(
    {"access_token", "refresh_token", "token_type", "must_change_password"}
)
_AUTH_STATUS_FIELDS = frozenset(
    {"initialized", "default_username", "requires_password_change"}
)
_HEALTH_REQUIRED_FIELDS = frozenset(
    {
        "status",
        "timestamp",
        "service",
        "chat_only",
        "desktop_protocol_version",
        "desktop_manageability_version",
        "supports_desktop_auth",
        "supports_desktop_backend_ownership",
        "studio_root_id",
        "native_path_leases_supported",
        "chat_only_reason",
        "version",
        "studio_version",
        "device_type",
        "cloudflare_url",
        "server_url",
        "secure",
    }
)
_HEALTH_OPTIONAL_FIELDS = frozenset({"desktop_owner"})
_MCP_TOOL_FIELDS = frozenset(
    {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "icons",
        "_meta",
    }
)


class IncompatibleUnslothStudioContract(ValueError):
    """The upstream response does not match the pinned integration contract."""


def validate_studio_token(value: Mapping[str, Any]) -> dict[str, Any]:
    data = _closed_mapping(value, _TOKEN_FIELDS, "Studio token")
    access_token = _bounded_token(data.get("access_token"), "access_token")
    refresh_token = _bounded_token(data.get("refresh_token"), "refresh_token")
    if data.get("token_type") != "bearer" or not isinstance(
        data.get("must_change_password"), bool
    ):
        raise IncompatibleUnslothStudioContract("Studio token metadata is incompatible")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "must_change_password": data["must_change_password"],
    }


def validate_auth_status(value: Mapping[str, Any]) -> dict[str, Any]:
    data = _closed_mapping(value, _AUTH_STATUS_FIELDS, "Studio auth status")
    if (
        not isinstance(data.get("initialized"), bool)
        or data.get("default_username") != "unsloth"
        or not isinstance(data.get("requires_password_change"), bool)
    ):
        raise IncompatibleUnslothStudioContract("Studio auth status is incompatible")
    return {
        "initialized": data["initialized"],
        "default_username": "unsloth",
        "requires_password_change": data["requires_password_change"],
    }


def validate_studio_health(
    value: Mapping[str, Any],
    *,
    expected_studio_version: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IncompatibleUnslothStudioContract("Studio health must be an object")
    fields = set(value)
    if (
        not _HEALTH_REQUIRED_FIELDS.issubset(fields)
        or not fields.issubset(_HEALTH_REQUIRED_FIELDS | _HEALTH_OPTIONAL_FIELDS)
    ):
        raise IncompatibleUnslothStudioContract("Studio health fields are incompatible")
    data = dict(value)
    if data.get("status") != "healthy" or data.get("service") != "Unsloth UI Backend":
        raise IncompatibleUnslothStudioContract("Studio health identity is incompatible")
    try:
        datetime.fromisoformat(str(data.get("timestamp") or ""))
    except ValueError as exc:
        raise IncompatibleUnslothStudioContract("Studio health timestamp is invalid") from exc
    if (
        data.get("desktop_protocol_version") != 1
        or data.get("desktop_manageability_version") != 2
        or data.get("supports_desktop_auth") is not True
        or data.get("supports_desktop_backend_ownership") is not True
        or not isinstance(data.get("chat_only"), bool)
        or not isinstance(data.get("native_path_leases_supported"), bool)
        or not isinstance(data.get("secure"), bool)
    ):
        raise IncompatibleUnslothStudioContract("Studio health capability bits are incompatible")
    if data.get("cloudflare_url") is not None or data.get("secure") is not False:
        raise IncompatibleUnslothStudioContract("Studio tunnel exposure is forbidden")
    studio_version = _bounded_version(data.get("studio_version"), "studio_version")
    if studio_version != expected_studio_version:
        raise IncompatibleUnslothStudioContract("Studio version is incompatible")
    unsloth_version = _bounded_version(data.get("version"), "version")
    if data.get("device_type") not in {"linux", "mac", "windows"}:
        raise IncompatibleUnslothStudioContract("Studio device type is incompatible")
    for field in ("studio_root_id", "chat_only_reason", "server_url"):
        _bounded_optional_text(data.get(field), field, maximum=512)
    if "desktop_owner" in data:
        owner = _closed_mapping(
            data["desktop_owner"],
            frozenset({"kind", "token_sha256"}),
            "Studio desktop owner",
        )
        if (
            owner.get("kind") != "tauri"
            or not isinstance(owner.get("token_sha256"), str)
            or len(owner["token_sha256"]) != 64
        ):
            raise IncompatibleUnslothStudioContract("Studio desktop owner is incompatible")
    return {
        "schema_version": UNSLOTH_STUDIO_PROBE_SCHEMA_VERSION,
        "upstream_contract_version": UNSLOTH_STUDIO_UPSTREAM_CONTRACT_VERSION,
        "status": "ready",
        "unsloth_version": unsloth_version,
        "studio_version": studio_version,
        "device_type": data["device_type"],
        "chat_only": data["chat_only"],
        "native_path_leases_supported": data["native_path_leases_supported"],
        "remote_tunnel_enabled": False,
    }


def compose_studio_probe(
    *,
    auth_status: Mapping[str, Any],
    health: Mapping[str, Any],
    expected_studio_version: str,
) -> dict[str, Any]:
    auth = validate_auth_status(auth_status)
    if (
        auth["initialized"] is not True
        or auth["requires_password_change"] is not False
    ):
        raise IncompatibleUnslothStudioContract("Studio authentication is not production-ready")
    probe = validate_studio_health(
        health,
        expected_studio_version=expected_studio_version,
    )
    return {
        **probe,
        "authentication": {
            "mode": "jwt_login_refresh",
            "initialized": True,
            "default_username": "unsloth",
            "requires_password_change": False,
        },
        "capabilities": {
            "health": True,
            "version": True,
            "jwt_refresh": True,
        },
    }


def validate_mcp_tools_list(
    value: Mapping[str, Any],
    *,
    request_id: str,
    required_tools: Sequence[str],
    required_tool_schemas: Mapping[
        str,
        Mapping[str, Any],
    ] | None = None,
) -> dict[str, Any]:
    data = _closed_mapping(
        value,
        frozenset({"jsonrpc", "id", "result"}),
        "MCP tools/list response",
    )
    if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
        raise IncompatibleUnslothStudioContract("MCP response correlation is incompatible")
    result = _closed_mapping(
        data.get("result"),
        frozenset({"tools"}),
        "MCP tools/list result",
    )
    tools = result.get("tools")
    if not isinstance(tools, list) or len(tools) > 128:
        raise IncompatibleUnslothStudioContract("MCP tool list is incompatible")
    names: list[str] = []
    schemas = dict(required_tool_schemas or {})
    for item in tools:
        if not isinstance(item, Mapping):
            raise IncompatibleUnslothStudioContract("MCP tool descriptor is incompatible")
        fields = set(item)
        if (
            not {"name", "inputSchema"}.issubset(fields)
            or not fields.issubset(_MCP_TOOL_FIELDS)
        ):
            raise IncompatibleUnslothStudioContract("MCP tool descriptor fields are incompatible")
        name = _bounded_identifier(item.get("name"), "MCP tool name")
        schema = item.get("inputSchema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise IncompatibleUnslothStudioContract("MCP input schema is incompatible")
        expected_schema = schemas.get(name)
        if expected_schema is not None:
            _validate_required_mcp_tool_schema(
                schema,
                expected_schema,
            )
        names.append(name)
    if len(names) != len(set(names)) or not set(required_tools).issubset(names):
        raise IncompatibleUnslothStudioContract("required MCP tools are unavailable")
    return {
        "schema_version": "ananta.unsloth-mcp-probe.v1",
        "upstream_contract_version": UNSLOTH_STUDIO_UPSTREAM_CONTRACT_VERSION,
        "available": True,
        "tools": sorted(set(required_tools)),
    }


def _validate_required_mcp_tool_schema(
    upstream: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    upstream_properties = upstream.get("properties", {})
    expected_properties = expected.get("properties", {})
    upstream_required = upstream.get("required", [])
    expected_required = expected.get("required", [])
    if (
        not isinstance(upstream_properties, Mapping)
        or not isinstance(expected_properties, Mapping)
        or not isinstance(upstream_required, list)
        or not isinstance(expected_required, list)
        or upstream.get("additionalProperties", False) is True
        or set(upstream_properties) != set(expected_properties)
        or set(upstream_required) != set(expected_required)
    ):
        raise IncompatibleUnslothStudioContract(
            "MCP input schema is incompatible"
        )
    for name, expected_property in expected_properties.items():
        upstream_property = upstream_properties.get(name)
        if (
            not isinstance(expected_property, Mapping)
            or not isinstance(upstream_property, Mapping)
            or upstream_property.get("type")
            != expected_property.get("type")
        ):
            raise IncompatibleUnslothStudioContract(
                "MCP input schema is incompatible"
            )


def validate_mcp_call_result(
    value: Mapping[str, Any],
    *,
    request_id: str,
) -> Any:
    if not isinstance(value, Mapping):
        raise IncompatibleUnslothStudioContract("MCP tool result must be an object")
    fields = set(value)
    if fields == {"jsonrpc", "id", "error"}:
        if value.get("jsonrpc") != "2.0" or value.get("id") != request_id:
            raise IncompatibleUnslothStudioContract("MCP error correlation is incompatible")
        raise IncompatibleUnslothStudioContract("MCP tool returned an error")
    data = _closed_mapping(
        value,
        frozenset({"jsonrpc", "id", "result"}),
        "MCP tool response",
    )
    if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
        raise IncompatibleUnslothStudioContract("MCP response correlation is incompatible")
    return data["result"]


def _closed_mapping(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise IncompatibleUnslothStudioContract(f"{name} fields are incompatible")
    return dict(value)


def _bounded_token(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 4096
        or "\r" in value
        or "\n" in value
    ):
        raise IncompatibleUnslothStudioContract(f"Studio {name} is invalid")
    return value


def _bounded_version(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(not character.isprintable() for character in value)
    ):
        raise IncompatibleUnslothStudioContract(f"Studio {name} is invalid")
    return value


def _bounded_identifier(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(
            not (character.isalnum() or character in {"_", "-", ".", ":"})
            for character in value
        )
    ):
        raise IncompatibleUnslothStudioContract(f"{name} is invalid")
    return value


def _bounded_optional_text(value: Any, name: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(not character.isprintable() for character in value)
    ):
        raise IncompatibleUnslothStudioContract(f"Studio {name} is invalid")
    return value
