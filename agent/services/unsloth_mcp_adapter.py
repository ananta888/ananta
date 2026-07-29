"""Policy-enforcing MCP adapter for optional Unsloth Studio integration."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.unsloth_studio import (
    IncompatibleUnslothStudioContract,
    UNSLOTH_MCP_PROTOCOL_VERSION,
    validate_mcp_call_result,
    validate_mcp_tools_list,
)
from ananta_contracts.mcp_streamable_http import (
    McpStreamableHttpTransport,
)
from agent.common.redaction import VisibilityLevel, redact
from agent.services.opaque_secret_reference_service import (
    OpaqueSecretReferenceError,
    OpaqueSecretReferenceService,
    opaque_secret_reference_service,
)
from agent.services.unsloth_studio_transport import (
    UnslothStudioTransport,
    UnslothStudioTransportError,
)
from agent.services.unsloth_studio_worker_adapter import (
    UnslothStudioWorkerAdapter,
    UnslothStudioWorkerAdapterError,
)
from agent.services.workflow_runtime.security import ReplayNonceStore

_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROPERTY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+"
)
_FORBIDDEN_ARGUMENT_KEY_RE = re.compile(
    r"(?:^|_)(?:path|url|uri|shell|command|cmd|arg|args|token|secret|password)"
    r"(?:_|$)",
    re.IGNORECASE,
)
_FORBIDDEN_STRING_RE = re.compile(r"[\r\n/\\;|&`$<>]")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)
_EMPTY_OBJECT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


class UnslothMcpError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "unsloth_mcp_failed")
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class UnslothMcpToolPolicy:
    tool_id: str
    access_class: str
    allowed_roles: tuple[str, ...]
    argument_schema: Mapping[str, Any] = field(
        default_factory=lambda: dict(_EMPTY_OBJECT_SCHEMA)
    )
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        tool_id = str(self.tool_id or "").strip()
        if _TOOL_ID_RE.fullmatch(tool_id) is None:
            raise ValueError("unsloth_mcp_tool_id_invalid")
        access = str(self.access_class or "").strip().lower()
        if access not in {"read", "mutation"}:
            raise ValueError("unsloth_mcp_access_class_invalid")
        roles = tuple(sorted({_normalize_role(value) for value in self.allowed_roles}))
        if not roles:
            raise ValueError("unsloth_mcp_role_allowlist_required")
        if access == "mutation" and (
            "admin" not in roles or not self.requires_confirmation
        ):
            raise ValueError("unsloth_mcp_mutation_policy_unsafe")
        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "access_class", access)
        object.__setattr__(self, "allowed_roles", roles)
        object.__setattr__(
            self,
            "argument_schema",
            _validate_argument_schema(self.argument_schema),
        )


class UnslothMcpAdapter:
    def __init__(
        self,
        *,
        transport: UnslothStudioTransport,
        studio_adapter: UnslothStudioWorkerAdapter,
        replay_store: ReplayNonceStore,
        mcp_bearer_secret_ref: str,
        tool_policies: Sequence[UnslothMcpToolPolicy],
        secret_resolver: OpaqueSecretReferenceService = opaque_secret_reference_service,
        audit_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        mcp_path: str = "/mcp/",
        maximum_output_bytes: int = 64 * 1024,
        maximum_replay_ttl_seconds: float = 300.0,
        probe_cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        reference = str(mcp_bearer_secret_ref or "").strip()
        if not reference:
            raise ValueError("unsloth_mcp_bearer_secret_ref_required")
        if str(mcp_path or "") != "/mcp/":
            raise ValueError("unsloth_mcp_path_invalid")
        if not 1024 <= int(maximum_output_bytes) <= 1024 * 1024:
            raise ValueError("unsloth_mcp_output_limit_invalid")
        if not 1 <= float(maximum_replay_ttl_seconds) <= 300:
            raise ValueError("unsloth_mcp_replay_ttl_invalid")
        if not 1 <= float(probe_cache_ttl_seconds) <= 60:
            raise ValueError("unsloth_mcp_probe_cache_ttl_invalid")
        policies: dict[str, UnslothMcpToolPolicy] = {}
        for policy in tool_policies:
            if policy.tool_id in policies:
                raise ValueError("unsloth_mcp_tool_policy_duplicate")
            policies[policy.tool_id] = policy
        if not policies:
            raise ValueError("unsloth_mcp_tool_allowlist_required")
        self._transport = McpStreamableHttpTransport(
            transport
        )
        self._studio_adapter = studio_adapter
        self._replay_store = replay_store
        self._mcp_bearer_secret_ref = reference
        self._policies = policies
        self._secret_resolver = secret_resolver
        self._audit_sink = audit_sink
        self._mcp_path = "/mcp/"
        self._maximum_output_bytes = int(maximum_output_bytes)
        self._maximum_replay_ttl_seconds = float(maximum_replay_ttl_seconds)
        self._probe_cache_ttl_seconds = float(probe_cache_ttl_seconds)
        self._clock = clock
        self._probe_lock = threading.RLock()
        self._probe_cache: tuple[float, Mapping[str, Any]] | None = None

    def probe(self) -> Mapping[str, Any]:
        now = float(self._clock())
        with self._probe_lock:
            if self._probe_cache is not None and self._probe_cache[0] > now:
                return dict(self._probe_cache[1])
            self._ensure_mcp_bearer_available()
            request_id = "probe-tools-list-v1"
            try:
                raw = self._transport.request_json(
                    method="POST",
                    path=self._mcp_path,
                    payload={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/list",
                        "params": {},
                    },
                    service_bearer_secret_ref=self._mcp_bearer_secret_ref,
                )
                validate_mcp_tools_list(
                    raw,
                    request_id=request_id,
                    required_tools=tuple(sorted(self._policies)),
                    required_tool_schemas={
                        tool_id: policy.argument_schema
                        for tool_id, policy in self._policies.items()
                    },
                )
            except IncompatibleUnslothStudioContract as exc:
                raise UnslothMcpError("incompatible_upstream_contract") from exc
            except UnslothStudioTransportError as exc:
                raise UnslothMcpError(
                    _mcp_transport_reason(
                        exc.reason_code,
                        unavailable="unsloth_mcp_probe_unavailable",
                    )
                ) from exc
            result = {
                "schema_version": "ananta.unsloth-mcp-probe.v1",
                "available": True,
                "reason_code": None,
                "protocol_version": UNSLOTH_MCP_PROTOCOL_VERSION,
                "tool_ids": sorted(self._policies),
            }
            self._probe_cache = (
                now + self._probe_cache_ttl_seconds,
                result,
            )
            return dict(result)

    def execute(
        self,
        *,
        tool_id: str,
        arguments: Mapping[str, Any] | None,
        tenant_id: str,
        actor_id: str,
        roles: Sequence[str],
        replay_nonce: str,
        replay_expires_at: float,
        correlation_id: str,
        confirmation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        normalized_correlation = str(correlation_id or "").strip()
        if _CORRELATION_RE.fullmatch(normalized_correlation) is None:
            raise UnslothMcpError("unsloth_mcp_correlation_id_invalid")
        normalized_tool = str(tool_id or "")
        try:
            result = self._execute(
                tool_id=normalized_tool,
                arguments=arguments,
                tenant_id=tenant_id,
                actor_id=actor_id,
                roles=roles,
                replay_nonce=replay_nonce,
                replay_expires_at=replay_expires_at,
                correlation_id=normalized_correlation,
                confirmation_id=confirmation_id,
                idempotency_key=idempotency_key,
            )
        except UnslothMcpError as exc:
            self._audit(
                "unsloth_mcp_execution_denied",
                tenant_id=tenant_id,
                actor_id=actor_id,
                tool_id=normalized_tool,
                correlation_id=normalized_correlation,
                reason_code=exc.reason_code,
            )
            raise
        self._audit(
            "unsloth_mcp_execution_completed",
            tenant_id=tenant_id,
            actor_id=actor_id,
            tool_id=normalized_tool,
            correlation_id=normalized_correlation,
            reason_code="ok",
        )
        return result

    def _execute(
        self,
        *,
        tool_id: str,
        arguments: Mapping[str, Any] | None,
        tenant_id: str,
        actor_id: str,
        roles: Sequence[str],
        replay_nonce: str,
        replay_expires_at: float,
        correlation_id: str,
        confirmation_id: str | None,
        idempotency_key: str | None,
    ) -> Mapping[str, Any]:
        policy = self._policies.get(tool_id)
        if policy is None:
            raise UnslothMcpError("unsloth_mcp_tool_not_allowlisted")
        actor_roles = frozenset(_normalize_role(value) for value in roles)
        if not actor_roles.intersection(policy.allowed_roles):
            raise UnslothMcpError("unsloth_mcp_role_denied")
        normalized_arguments = _validate_arguments(
            policy.argument_schema,
            arguments,
        )
        if policy.access_class == "mutation":
            if "admin" not in actor_roles:
                raise UnslothMcpError("unsloth_mcp_admin_role_required")
            normalized_confirmation = str(
                confirmation_id or ""
            ).strip()
            if not normalized_confirmation:
                raise UnslothMcpError(
                    "unsloth_mcp_confirmation_required"
                )
            if (
                re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                    normalized_confirmation,
                )
                is None
            ):
                raise UnslothMcpError(
                    "unsloth_mcp_confirmation_invalid"
                )
            if _IDEMPOTENCY_KEY_RE.fullmatch(str(idempotency_key or "")) is None:
                raise UnslothMcpError("unsloth_mcp_idempotency_key_required")
        self.probe()
        self._consume_replay_nonce(
            tenant_id=tenant_id,
            nonce=replay_nonce,
            expires_at=replay_expires_at,
        )

        if policy.access_class == "read":
            try:
                raw = self._transport.request_json(
                    method="POST",
                    path=self._mcp_path,
                    payload={
                        "jsonrpc": "2.0",
                        "id": str(replay_nonce),
                        "method": "tools/call",
                        "params": {
                            "name": policy.tool_id,
                            "arguments": normalized_arguments,
                        },
                    },
                    service_bearer_secret_ref=self._mcp_bearer_secret_ref,
                )
                content = validate_mcp_call_result(
                    raw,
                    request_id=str(replay_nonce),
                )
            except IncompatibleUnslothStudioContract as exc:
                raise UnslothMcpError("incompatible_upstream_contract") from exc
            except UnslothStudioTransportError as exc:
                raise UnslothMcpError(
                    _mcp_transport_reason(
                        exc.reason_code,
                        unavailable="unsloth_mcp_upstream_unavailable",
                    )
                ) from exc
            return {
                "status": "ok",
                "tool_id": policy.tool_id,
                "access_class": "read",
                "correlation_id": correlation_id,
                "content": self._redact_and_bound(content),
            }

        try:
            receipt = self._studio_adapter.submit_mutation(
                mutation=policy.tool_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                payload={
                    "tool_id": policy.tool_id,
                    "arguments": normalized_arguments,
                    "confirmation_id": str(confirmation_id),
                    "replay_nonce": str(replay_nonce),
                    "correlation_id": correlation_id,
                },
                idempotency_key=str(idempotency_key),
            )
        except UnslothStudioWorkerAdapterError as exc:
            raise UnslothMcpError(exc.reason_code) from exc
        return {
            "status": "queued",
            "tool_id": policy.tool_id,
            "access_class": "mutation",
            "correlation_id": correlation_id,
            "receipt": self._redact_and_bound(receipt),
        }

    def _ensure_mcp_bearer_available(self) -> None:
        try:
            value = self._secret_resolver.resolve(self._mcp_bearer_secret_ref)
        except OpaqueSecretReferenceError as exc:
            raise UnslothMcpError(
                "unsloth_mcp_bearer_secret_unavailable"
            ) from exc
        if len(value) < 16 or len(value) > 4096 or "\r" in value or "\n" in value:
            raise UnslothMcpError("unsloth_mcp_bearer_secret_invalid")

    def _consume_replay_nonce(
        self,
        *,
        tenant_id: str,
        nonce: str,
        expires_at: float,
    ) -> None:
        now = float(self._clock())
        expiry = float(expires_at)
        if (
            _NONCE_RE.fullmatch(str(nonce or "")) is None
            or not math.isfinite(expiry)
            or expiry <= now
            or expiry > now + self._maximum_replay_ttl_seconds
        ):
            raise UnslothMcpError("unsloth_mcp_replay_window_invalid")
        if not self._replay_store.consume(
            tenant_id=str(tenant_id),
            nonce=str(nonce),
            expires_at=expiry,
        ):
            raise UnslothMcpError("unsloth_mcp_replay_detected")

    def _audit(
        self,
        event_type: str,
        *,
        tenant_id: str,
        actor_id: str,
        tool_id: str,
        correlation_id: str,
        reason_code: str,
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink(
            event_type,
            {
                "tenant_id": str(tenant_id),
                "actor_id": str(actor_id),
                "tool_id": str(tool_id),
                "correlation_id": str(correlation_id),
                "reason_code": str(reason_code),
            },
        )

    def _redact_and_bound(self, value: Any) -> Any:
        sanitized = _scrub(redact(value, VisibilityLevel.USER))
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        if len(encoded) <= self._maximum_output_bytes:
            return sanitized
        digest = hashlib.sha256(encoded).hexdigest()
        preview_limit = max(128, min(4096, self._maximum_output_bytes // 2))
        return {
            "truncated": True,
            "sha256": digest,
            "preview": encoded[:preview_limit].decode("utf-8", errors="replace"),
        }


def default_unsloth_mcp_tool_policies() -> tuple[UnslothMcpToolPolicy, ...]:
    read_roles = ("admin", "operator", "viewer")
    pagination = {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
    }
    job_id = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    }
    return (
        UnslothMcpToolPolicy(
            tool_id="studio_status",
            access_class="read",
            allowed_roles=read_roles,
        ),
        UnslothMcpToolPolicy(
            tool_id="get_training_status",
            access_class="read",
            allowed_roles=read_roles,
        ),
        UnslothMcpToolPolicy(
            tool_id="list_training_runs",
            access_class="read",
            allowed_roles=read_roles,
            argument_schema={
                "type": "object",
                "properties": pagination,
                "required": [],
                "additionalProperties": False,
            },
        ),
        UnslothMcpToolPolicy(
            tool_id="get_recipe_job_status",
            access_class="read",
            allowed_roles=read_roles,
            argument_schema={
                "type": "object",
                "properties": {"job_id": job_id},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        UnslothMcpToolPolicy(
            tool_id="get_recipe_job_dataset",
            access_class="read",
            allowed_roles=read_roles,
            argument_schema={
                "type": "object",
                "properties": {"job_id": job_id, **pagination},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        ),
        UnslothMcpToolPolicy(
            tool_id="stop_training",
            access_class="mutation",
            allowed_roles=("admin",),
            requires_confirmation=True,
            argument_schema={
                "type": "object",
                "properties": {"save": {"type": "boolean"}},
                "required": [],
                "additionalProperties": False,
            },
        ),
    )


def _validate_argument_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    if set(value) != {"type", "properties", "required", "additionalProperties"}:
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError("unsloth_mcp_argument_schema_must_be_closed")
    raw_properties = value.get("properties")
    raw_required = value.get("required")
    if not isinstance(raw_properties, Mapping) or len(raw_properties) > 32:
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    if (
        not isinstance(raw_required, Sequence)
        or isinstance(raw_required, (str, bytes))
    ):
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    properties: dict[str, dict[str, Any]] = {}
    for raw_name, raw_schema in raw_properties.items():
        name = str(raw_name)
        if (
            _PROPERTY_RE.fullmatch(name) is None
            or _FORBIDDEN_ARGUMENT_KEY_RE.search(name)
        ):
            raise ValueError("unsloth_mcp_argument_name_forbidden")
        properties[name] = _validate_scalar_schema(raw_schema)
    required = [str(name) for name in raw_required]
    if len(required) != len(set(required)) or not set(required).issubset(properties):
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    normalized = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if len(json.dumps(normalized, sort_keys=True).encode("utf-8")) > 16 * 1024:
        raise ValueError("unsloth_mcp_argument_schema_too_large")
    return normalized


def _validate_scalar_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    scalar_type = value.get("type")
    if scalar_type not in {"boolean", "integer", "string"}:
        raise ValueError("unsloth_mcp_argument_type_forbidden")
    allowed_keys = {
        "boolean": {"type", "enum"},
        "integer": {"type", "enum", "minimum", "maximum"},
        "string": {
            "type",
            "enum",
            "minLength",
            "maxLength",
            "pattern",
        },
    }[scalar_type]
    if not set(value).issubset(allowed_keys):
        raise ValueError("unsloth_mcp_argument_schema_invalid")
    normalized = dict(value)
    if scalar_type == "integer":
        minimum = value.get("minimum", -(2**31))
        maximum = value.get("maximum", 2**31 - 1)
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum > maximum
        ):
            raise ValueError("unsloth_mcp_argument_schema_invalid")
        normalized["minimum"] = minimum
        normalized["maximum"] = maximum
    if scalar_type == "string":
        minimum = value.get("minLength", 0)
        maximum = value.get("maxLength", 512)
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 0
            or maximum > 512
            or minimum > maximum
        ):
            raise ValueError("unsloth_mcp_argument_schema_invalid")
        pattern = value.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or len(pattern) > 256:
                raise ValueError("unsloth_mcp_argument_schema_invalid")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("unsloth_mcp_argument_schema_invalid") from exc
        normalized["minLength"] = minimum
        normalized["maxLength"] = maximum
    enum = value.get("enum")
    if enum is not None:
        if (
            not isinstance(enum, Sequence)
            or isinstance(enum, (str, bytes))
            or not 1 <= len(enum) <= 32
        ):
            raise ValueError("unsloth_mcp_argument_schema_invalid")
        for item in enum:
            _validate_scalar_value(scalar_type, item, normalized)
        normalized["enum"] = list(enum)
    return normalized


def _validate_arguments(
    schema: Mapping[str, Any],
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if arguments is not None and not isinstance(arguments, Mapping):
        raise UnslothMcpError("unsloth_mcp_arguments_invalid")
    normalized = dict(arguments or {})
    properties = dict(schema["properties"])
    if not set(schema["required"]).issubset(normalized):
        raise UnslothMcpError("unsloth_mcp_argument_required")
    if not set(normalized).issubset(properties):
        raise UnslothMcpError("unsloth_mcp_argument_not_allowed")
    for name, value in normalized.items():
        if _FORBIDDEN_ARGUMENT_KEY_RE.search(str(name)):
            raise UnslothMcpError("unsloth_mcp_argument_name_forbidden")
        _validate_scalar_value(properties[name]["type"], value, properties[name])
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UnslothMcpError("unsloth_mcp_arguments_invalid") from exc
    if len(encoded) > 16 * 1024:
        raise UnslothMcpError("unsloth_mcp_arguments_too_large")
    return normalized


def _validate_scalar_value(
    scalar_type: str,
    value: Any,
    schema: Mapping[str, Any],
) -> None:
    valid = {
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string": isinstance(value, str),
    }[scalar_type]
    if not valid:
        raise UnslothMcpError("unsloth_mcp_argument_type_invalid")
    if scalar_type == "integer" and not (
        int(schema["minimum"]) <= value <= int(schema["maximum"])
    ):
        raise UnslothMcpError("unsloth_mcp_argument_range_invalid")
    if scalar_type == "string":
        if not int(schema["minLength"]) <= len(value) <= int(schema["maxLength"]):
            raise UnslothMcpError("unsloth_mcp_argument_range_invalid")
        if (
            "://" in value
            or value.startswith((".", "~"))
            or _FORBIDDEN_STRING_RE.search(value)
        ):
            raise UnslothMcpError("unsloth_mcp_argument_value_forbidden")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(str(pattern), value) is None:
            raise UnslothMcpError("unsloth_mcp_argument_pattern_invalid")
    if "enum" in schema and value not in schema["enum"]:
        raise UnslothMcpError("unsloth_mcp_argument_enum_invalid")


def _normalize_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if not role or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", role):
        raise ValueError("unsloth_mcp_role_invalid")
    return role


def _mcp_transport_reason(reason_code: str, *, unavailable: str) -> str:
    normalized = str(reason_code or "")
    if (
        normalized == "incompatible_upstream_contract"
        or normalized.startswith("unsloth_studio_response_")
    ):
        return "incompatible_upstream_contract"
    return unavailable


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _SENSITIVE_KEYS:
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _scrub(item)
        return result
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        scrubbed = _BEARER_RE.sub("Bearer [REDACTED]", value)
        return _INLINE_SECRET_RE.sub(
            lambda match: f"{match.group(1)}=[REDACTED]",
            scrubbed,
        )
    return value


__all__ = [
    "UnslothMcpAdapter",
    "UnslothMcpError",
    "UnslothMcpToolPolicy",
    "default_unsloth_mcp_tool_policies",
]
