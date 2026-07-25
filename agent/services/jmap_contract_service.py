from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


JMAP_CORE_CAPABILITY = "urn:ietf:params:jmap:core"
JMAP_MAIL_CAPABILITY = "urn:ietf:params:jmap:mail"


class JmapContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "jmap_contract_invalid")
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class JmapCoreLimits:
    maximum_request_bytes: int
    maximum_concurrent_requests: int
    maximum_calls_per_request: int
    maximum_objects_per_get: int
    maximum_objects_per_set: int
    maximum_queued_requests: int = 32
    request_queue_timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class JmapSessionDocument:
    session_url: str
    api_url: str
    download_url_template: str
    upload_url_template: str
    event_source_url_template: str
    provider_account_id: str
    server_capabilities: frozenset[str]
    account_capabilities: frozenset[str]
    limits: JmapCoreLimits
    state: str
    trusted_origin: str


@dataclass(frozen=True, slots=True)
class JmapResultReference:
    result_of: str
    name: str
    path: str

    def __post_init__(self) -> None:
        if (
            not _safe_reference_text(self.result_of)
            or not _safe_reference_text(self.name)
            or not _valid_json_pointer(self.path)
        ):
            raise JmapContractError("jmap_result_reference_invalid")

    def to_mapping(self) -> dict[str, str]:
        return {
            "resultOf": self.result_of,
            "name": self.name,
            "path": self.path,
        }

    @classmethod
    def from_value(cls, value: Any) -> "JmapResultReference":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or set(value) != {"resultOf", "name", "path"}:
            raise JmapContractError("jmap_result_reference_invalid")
        if any(not isinstance(value[key], str) for key in ("resultOf", "name", "path")):
            raise JmapContractError("jmap_result_reference_invalid")
        return cls(
            result_of=value["resultOf"],
            name=value["name"],
            path=value["path"],
        )


@dataclass(frozen=True, slots=True)
class JmapMethodCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str

    @classmethod
    def build(
        cls,
        *,
        name: str,
        arguments: Mapping[str, Any],
        call_id: str,
        result_references: Mapping[str, JmapResultReference] | None = None,
    ) -> "JmapMethodCall":
        values = dict(arguments)
        for argument_name, reference in dict(result_references or {}).items():
            clean_name = str(argument_name)
            if (
                not _ARGUMENT_NAME_RE.fullmatch(clean_name)
                or clean_name in values
                or f"#{clean_name}" in values
                or not isinstance(reference, JmapResultReference)
            ):
                raise JmapContractError("jmap_result_reference_argument_invalid")
            values[f"#{clean_name}"] = reference
        return cls(name=str(name), arguments=values, call_id=str(call_id))


@dataclass(frozen=True, slots=True)
class JmapMethodResponse:
    name: str
    arguments: Mapping[str, Any]
    call_id: str

    @property
    def is_error(self) -> bool:
        return self.name == "error"

    @property
    def error_type(self) -> str:
        return str(self.arguments.get("type") or "") if self.is_error else ""


def normalize_jmap_session(
    payload: Mapping[str, Any],
    *,
    session_url: str,
    trusted_origin: str,
    preferred_provider_account_id: str = "",
    local_maximum_request_bytes: int,
    local_maximum_calls_per_request: int,
    local_maximum_objects_per_get: int,
    local_maximum_objects_per_set: int,
    local_maximum_concurrent_requests: int = 4,
    local_maximum_queued_requests: int = 32,
    request_queue_timeout_seconds: float = 5.0,
) -> JmapSessionDocument:
    capabilities = _mapping(payload.get("capabilities"), "jmap_session_capabilities_invalid")
    if JMAP_CORE_CAPABILITY not in capabilities or JMAP_MAIL_CAPABILITY not in capabilities:
        raise JmapContractError("jmap_capability_missing")
    accounts = _mapping(payload.get("accounts"), "jmap_session_accounts_invalid")
    primary = _mapping(payload.get("primaryAccounts"), "jmap_session_primary_accounts_invalid")
    provider_account_id = str(preferred_provider_account_id or primary.get(JMAP_MAIL_CAPABILITY) or "").strip()
    if not provider_account_id or provider_account_id not in accounts:
        raise JmapContractError("jmap_mail_account_missing")
    provider_account = _mapping(accounts[provider_account_id], "jmap_session_account_invalid")
    account_capabilities = _mapping(
        provider_account.get("accountCapabilities"),
        "jmap_account_capabilities_invalid",
    )
    if JMAP_MAIL_CAPABILITY not in account_capabilities:
        raise JmapContractError("jmap_account_mail_capability_missing")
    core = _mapping(capabilities[JMAP_CORE_CAPABILITY], "jmap_core_capability_invalid")
    limits = JmapCoreLimits(
        maximum_request_bytes=_positive_bounded(
            core.get("maxSizeRequest"),
            local_maximum_request_bytes,
        ),
        maximum_concurrent_requests=_positive_bounded(
            core.get("maxConcurrentRequests"),
            local_maximum_concurrent_requests,
        ),
        maximum_calls_per_request=_positive_bounded(
            core.get("maxCallsInRequest"),
            local_maximum_calls_per_request,
        ),
        maximum_objects_per_get=_positive_bounded(
            core.get("maxObjectsInGet"),
            local_maximum_objects_per_get,
        ),
        maximum_objects_per_set=_positive_bounded(
            core.get("maxObjectsInSet"),
            local_maximum_objects_per_set,
        ),
        maximum_queued_requests=max(1, int(local_maximum_queued_requests)),
        request_queue_timeout_seconds=max(0.01, float(request_queue_timeout_seconds)),
    )
    return JmapSessionDocument(
        session_url=str(session_url),
        api_url=_required_text(payload.get("apiUrl"), "jmap_api_url_missing"),
        download_url_template=_required_text(payload.get("downloadUrl"), "jmap_download_url_missing"),
        upload_url_template=_required_text(payload.get("uploadUrl"), "jmap_upload_url_missing"),
        event_source_url_template=str(payload.get("eventSourceUrl") or ""),
        provider_account_id=provider_account_id,
        server_capabilities=frozenset(str(key) for key in capabilities),
        account_capabilities=frozenset(str(key) for key in account_capabilities),
        limits=limits,
        state=_required_text(payload.get("state"), "jmap_session_state_missing"),
        trusted_origin=str(trusted_origin),
    )


def parse_method_responses(
    payload: Mapping[str, Any],
    *,
    expected_calls: Sequence[JmapMethodCall],
) -> tuple[JmapMethodResponse, ...]:
    raw_responses = payload.get("methodResponses")
    if not isinstance(raw_responses, list):
        raise JmapContractError("jmap_method_responses_invalid")
    expected = {call.call_id for call in expected_calls}
    by_id: dict[str, JmapMethodResponse] = {}
    for raw in raw_responses:
        if not isinstance(raw, list) or len(raw) != 3:
            raise JmapContractError("jmap_method_response_invalid")
        name, arguments, call_id = raw
        clean_id = str(call_id)
        if clean_id not in expected:
            raise JmapContractError("jmap_method_response_unknown_call_id")
        if clean_id in by_id:
            raise JmapContractError("jmap_method_response_duplicate_call_id")
        if not isinstance(arguments, Mapping):
            raise JmapContractError("jmap_method_response_arguments_invalid")
        by_id[clean_id] = JmapMethodResponse(
            name=str(name),
            arguments=dict(arguments),
            call_id=clean_id,
        )
    if set(by_id) != expected:
        raise JmapContractError("jmap_method_response_missing")
    return tuple(by_id[call.call_id] for call in expected_calls)


def normalize_method_calls(
    calls: Sequence[JmapMethodCall],
) -> tuple[JmapMethodCall, ...]:
    normalized = tuple(calls)
    positions = {call.call_id: index for index, call in enumerate(normalized)}
    previous_methods: dict[str, str] = {}
    result: list[JmapMethodCall] = []
    for index, call in enumerate(normalized):
        arguments: dict[str, Any] = {}
        for raw_key, value in call.arguments.items():
            if not isinstance(raw_key, str):
                raise JmapContractError("jmap_method_argument_name_invalid")
            if not raw_key.startswith("#"):
                if isinstance(value, JmapResultReference):
                    raise JmapContractError("jmap_result_reference_argument_invalid")
                arguments[raw_key] = value
                continue
            argument_name = raw_key[1:]
            if (
                not _ARGUMENT_NAME_RE.fullmatch(argument_name)
                or argument_name in call.arguments
            ):
                raise JmapContractError("jmap_result_reference_argument_invalid")
            reference = JmapResultReference.from_value(value)
            referenced_position = positions.get(reference.result_of)
            if referenced_position is None:
                raise JmapContractError("jmap_result_reference_unknown_call")
            if referenced_position >= index:
                raise JmapContractError("jmap_result_reference_forward_call")
            expected_method = previous_methods.get(reference.result_of)
            if expected_method != reference.name:
                raise JmapContractError("jmap_result_reference_method_mismatch")
            arguments[raw_key] = reference.to_mapping()
        result.append(
            JmapMethodCall(
                name=call.name,
                arguments=arguments,
                call_id=call.call_id,
            )
        )
        previous_methods[call.call_id] = call.name
    return tuple(result)


def _mapping(value: Any, reason_code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise JmapContractError(reason_code)
    return value


def _required_text(value: Any, reason_code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JmapContractError(reason_code)
    return value.strip()


def _positive_bounded(server_value: Any, local_limit: int) -> int:
    try:
        parsed = int(server_value)
    except (TypeError, ValueError):
        parsed = int(local_limit)
    if parsed <= 0:
        raise JmapContractError("jmap_core_limit_invalid")
    return min(parsed, int(local_limit))


_ARGUMENT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _safe_reference_text(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and all(ord(character) >= 0x20 for character in value)
    )


def _valid_json_pointer(value: str) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    if value == "":
        return True
    if not value.startswith("/"):
        return False
    index = 0
    while index < len(value):
        if value[index] != "~":
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
            return False
        index += 2
    return True


__all__ = [
    "JMAP_CORE_CAPABILITY",
    "JMAP_MAIL_CAPABILITY",
    "JmapContractError",
    "JmapCoreLimits",
    "JmapMethodCall",
    "JmapMethodResponse",
    "JmapResultReference",
    "JmapSessionDocument",
    "normalize_jmap_session",
    "normalize_method_calls",
    "parse_method_responses",
]
