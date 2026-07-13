"""Worker-side LangGraph saver backed exclusively by the Hub checkpoint API."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import ssl
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from ananta_contracts.langgraph_checkpoint import (
    LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
    LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA,
    MAX_LANGGRAPH_CHECKPOINT_HISTORY,
    LangGraphCheckpointBinding,
    LangGraphCheckpointContractError,
    LangGraphCheckpointSnapshot,
    assert_json_mapping,
    assert_langgraph_config_binding,
)

try:  # Optional runtime dependency; importing this module stays dependency-safe.
    from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
except ImportError:  # pragma: no cover - the fallback is exercised without the extra installed.

    class BaseCheckpointSaver:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    class CheckpointTuple(NamedTuple):  # type: ignore[no-redef]
        config: dict[str, Any]
        checkpoint: dict[str, Any]
        metadata: dict[str, Any]
        parent_config: dict[str, Any] | None = None
        pending_writes: list[tuple[str, str, Any]] | None = None


_SERDE_VALUE_SCHEMA = "ananta.langgraph_serde_value.v1"
_SERDE_VALUE_KEY = "$ananta_langgraph_serde"


class LangGraphCheckpointGatewayError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = str(reason_code or "langgraph_checkpoint_gateway_failed")
        self.retryable = bool(retryable)
        super().__init__(self.reason_code)


class LangGraphCheckpointGatewayPort(Protocol):
    def get(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
    ) -> LangGraphCheckpointSnapshot | None: ...

    def list(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        metadata_filter: Mapping[str, Any],
        before_config: Mapping[str, Any] | None,
        limit: int,
    ) -> tuple[LangGraphCheckpointSnapshot, ...]: ...

    def put(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        metadata: Mapping[str, Any],
        expected_revision: int,
    ) -> LangGraphCheckpointSnapshot: ...

    def put_writes(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        pending_writes: Sequence[tuple[str, str, Any]],
        expected_revision: int,
    ) -> LangGraphCheckpointSnapshot: ...


class HttpLangGraphCheckpointGateway:
    """Bounded, bearer-authenticated, POST-only checkpoint transport."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        command_path: str = "/api/internal/workflow-runtime/langgraph/checkpoints",
        timeout_seconds: float = 15.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(hub_url or "").rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("langgraph checkpoint hub URL is invalid")
        self._hub_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._bearer_token = str(bearer_token or "")
        token_bytes = self._bearer_token.encode("utf-8")
        if (
            not 32 <= len(token_bytes) <= 16_384
            or "\x00" in self._bearer_token
            or any(character.isspace() for character in self._bearer_token)
        ):
            raise ValueError("langgraph checkpoint bearer token is invalid")
        self._command_path = "/" + str(command_path or "").strip("/")
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self._ssl_context = ssl_context

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "HttpLangGraphCheckpointGateway | None":
        source = os.environ if env is None else env
        hub_url = str(source.get("ANANTA_LANGGRAPH_HUB_URL") or "").strip()
        token_file = str(source.get("ANANTA_LANGGRAPH_HUB_TOKEN_FILE") or "").strip()
        if not hub_url and not token_file:
            return None
        if not hub_url or not token_file or not Path(token_file).is_absolute():
            raise ValueError("langgraph checkpoint Hub URL and absolute token file are required")
        token_path = Path(token_file)
        try:
            metadata = token_path.stat()
        except OSError as exc:
            raise ValueError("langgraph checkpoint bearer token file cannot be inspected") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("langgraph checkpoint bearer token file is unsafe")
        try:
            with token_path.open("rb") as handle:
                raw_token = handle.read(16_385)
            token = raw_token.decode("utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("langgraph checkpoint bearer token file cannot be read") from exc
        if (
            not 32 <= len(token.encode("utf-8")) <= 16_384
            or "\x00" in token
            or any(character.isspace() for character in token)
        ):
            raise ValueError("langgraph checkpoint bearer token file is invalid")
        return cls(hub_url=hub_url, bearer_token=token)

    def get(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
    ) -> LangGraphCheckpointSnapshot | None:
        response = self._command("get", binding=binding, config=dict(config))
        snapshot = response.get("snapshot")
        return LangGraphCheckpointSnapshot.from_mapping(snapshot) if snapshot is not None else None

    def list(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        metadata_filter: Mapping[str, Any],
        before_config: Mapping[str, Any] | None,
        limit: int,
    ) -> tuple[LangGraphCheckpointSnapshot, ...]:
        response = self._command(
            "list",
            binding=binding,
            config=dict(config),
            metadata_filter=dict(metadata_filter),
            before_config=dict(before_config) if before_config is not None else None,
            limit=int(limit),
        )
        values = response.get("snapshots")
        if not isinstance(values, list):
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_response_invalid")
        try:
            return tuple(LangGraphCheckpointSnapshot.from_mapping(value) for value in values)
        except LangGraphCheckpointContractError as exc:
            raise LangGraphCheckpointGatewayError(exc.reason_code) from exc

    def put(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        metadata: Mapping[str, Any],
        expected_revision: int,
    ) -> LangGraphCheckpointSnapshot:
        response = self._command(
            "put",
            binding=binding,
            config=dict(config),
            checkpoint=dict(checkpoint),
            metadata=dict(metadata),
            expected_revision=int(expected_revision),
        )
        return self._required_snapshot(response)

    def put_writes(
        self,
        *,
        binding: LangGraphCheckpointBinding,
        config: Mapping[str, Any],
        pending_writes: Sequence[tuple[str, str, Any]],
        expected_revision: int,
    ) -> LangGraphCheckpointSnapshot:
        response = self._command(
            "put_writes",
            binding=binding,
            config=dict(config),
            pending_writes=[list(value) for value in pending_writes],
            expected_revision=int(expected_revision),
        )
        return self._required_snapshot(response)

    @staticmethod
    def _required_snapshot(response: Mapping[str, Any]) -> LangGraphCheckpointSnapshot:
        try:
            return LangGraphCheckpointSnapshot.from_mapping(response.get("snapshot"))
        except LangGraphCheckpointContractError as exc:
            raise LangGraphCheckpointGatewayError(exc.reason_code) from exc

    def _command(
        self,
        operation: str,
        *,
        binding: LangGraphCheckpointBinding,
        **values: Any,
    ) -> dict[str, Any]:
        payload = {
            "schema": LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
            "operation": str(operation),
            "binding": binding.to_dict(),
            **values,
        }
        try:
            body = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_json_invalid") from exc
        if len(body) > 262_144:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_payload_too_large")
        request = urllib.request.Request(
            self._hub_url + self._command_path,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": "ananta-langgraph-worker/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                raw = response.read(1_048_577)
        except urllib.error.HTTPError as exc:
            reason_code = _http_error_reason(exc)
            retryable = int(exc.code) >= 500 or int(exc.code) in {408, 425, 429}
            raise LangGraphCheckpointGatewayError(
                reason_code or f"langgraph_checkpoint_hub_http_{exc.code}",
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_hub_unavailable", retryable=True) from exc
        if len(raw) > 1_048_576:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_response_invalid") from exc
        data = decoded.get("data") if isinstance(decoded, Mapping) else None
        if not isinstance(data, Mapping) or data.get("schema") != LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_response_invalid")
        return dict(data)


class LangGraphHubOwnedCheckpointer(BaseCheckpointSaver):  # type: ignore[misc]
    """LangGraph ``BaseCheckpointSaver`` bound to one delegated Hub task."""

    def __init__(
        self,
        *,
        gateway: LangGraphCheckpointGatewayPort,
        binding: LangGraphCheckpointBinding,
    ) -> None:
        super().__init__()
        binding.validate()
        self._gateway = gateway
        self._binding = binding
        self._write_lock = threading.RLock()

    def get_tuple(self, config: Mapping[str, Any]) -> CheckpointTuple | None:
        normalized = assert_langgraph_config_binding(config, task_id=self._binding.task_id)
        snapshot = self._gateway.get(binding=self._binding, config=normalized)
        if snapshot is not None:
            _validate_snapshot_binding(snapshot, self._binding)
        return self._checkpoint_tuple(snapshot) if snapshot is not None else None

    def list(
        self,
        config: Mapping[str, Any] | None,
        *,
        filter: Mapping[str, Any] | None = None,
        before: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        normalized = assert_langgraph_config_binding(
            config or _base_config(self._binding.task_id),
            task_id=self._binding.task_id,
        )
        requested_limit = MAX_LANGGRAPH_CHECKPOINT_HISTORY if limit is None else int(limit)
        snapshots = self._gateway.list(
            binding=self._binding,
            config=normalized,
            metadata_filter=assert_json_mapping(filter or {}, reason_code="langgraph_checkpoint_filter_invalid"),
            before_config=(
                assert_langgraph_config_binding(before, task_id=self._binding.task_id) if before is not None else None
            ),
            limit=requested_limit,
        )
        for snapshot in snapshots:
            _validate_snapshot_binding(snapshot, self._binding)
        return iter(self._checkpoint_tuple(value) for value in snapshots)

    def put(
        self,
        config: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        metadata: Mapping[str, Any],
        new_versions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._write_lock:
            return self._put_serialized(
                config,
                checkpoint,
                metadata,
                new_versions,
            )

    def _put_serialized(
        self,
        config: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        metadata: Mapping[str, Any],
        new_versions: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        del new_versions
        normalized = assert_langgraph_config_binding(config, task_id=self._binding.task_id)
        latest = self._gateway.get(
            binding=self._binding,
            config=_without_checkpoint_id(normalized),
        )
        if latest is not None:
            _validate_snapshot_binding(latest, self._binding)
        snapshot = self._gateway.put(
            binding=self._binding,
            config=normalized,
            checkpoint=self._encode_mapping(
                checkpoint,
                reason_code="langgraph_checkpoint_payload_invalid",
            ),
            metadata=self._encode_mapping(
                metadata,
                reason_code="langgraph_checkpoint_metadata_invalid",
            ),
            expected_revision=latest.head_revision if latest is not None else 0,
        )
        _validate_snapshot_binding(snapshot, self._binding)
        return dict(snapshot.config)

    def put_writes(
        self,
        config: Mapping[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._write_lock:
            self._put_writes_serialized(config, writes, task_id, task_path)

    def _put_writes_serialized(
        self,
        config: Mapping[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str,
    ) -> None:
        del task_path
        normalized = assert_langgraph_config_binding(config, task_id=self._binding.task_id)
        latest = self._gateway.get(
            binding=self._binding,
            config=_without_checkpoint_id(normalized),
        )
        if latest is None:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_not_found")
        _validate_snapshot_binding(latest, self._binding)
        pending = tuple((str(task_id), str(channel), self._encode_value(value)) for channel, value in writes)
        snapshot = self._gateway.put_writes(
            binding=self._binding,
            config=normalized,
            pending_writes=pending,
            expected_revision=latest.head_revision,
        )
        _validate_snapshot_binding(snapshot, self._binding)

    def _checkpoint_tuple(
        self,
        snapshot: LangGraphCheckpointSnapshot,
    ) -> CheckpointTuple:
        checkpoint = self._decode_value(dict(snapshot.checkpoint))
        metadata = self._decode_value(dict(snapshot.metadata))
        if not isinstance(checkpoint, Mapping) or not isinstance(metadata, Mapping):
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_serialized_root_invalid")
        return CheckpointTuple(
            config=dict(snapshot.config),
            checkpoint=dict(checkpoint),
            metadata=dict(metadata),
            parent_config=(dict(snapshot.parent_config) if snapshot.parent_config is not None else None),
            pending_writes=[
                (task_id, channel, self._decode_value(value)) for task_id, channel, value in snapshot.pending_writes
            ],
        )

    def _encode_mapping(
        self,
        value: Mapping[str, Any],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise LangGraphCheckpointGatewayError(reason_code)
        encoded = self._encode_value(dict(value))
        if not isinstance(encoded, dict):
            raise LangGraphCheckpointGatewayError(reason_code)
        return assert_json_mapping(encoded, reason_code=reason_code)

    def _encode_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise LangGraphCheckpointGatewayError("langgraph_checkpoint_json_invalid")
            return value
        if isinstance(value, Mapping):
            if _is_serde_envelope(value):
                return self._serialize_opaque(value)
            encoded: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    return self._serialize_opaque(value)
                encoded[key] = self._encode_value(item)
            return encoded
        if isinstance(value, list):
            return [self._encode_value(item) for item in value]
        return self._serialize_opaque(value)

    def _serialize_opaque(self, value: Any) -> dict[str, Any]:
        serde = getattr(self, "serde", None)
        dumps_typed = getattr(serde, "dumps_typed", None)
        if not callable(dumps_typed):
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_value_not_serializable")
        try:
            value_type, payload = dumps_typed(value)
        except Exception as exc:  # noqa: BLE001 - dependency boundary
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_value_not_serializable") from exc
        if (
            not isinstance(value_type, str)
            or not value_type
            or len(value_type) > 128
            or not isinstance(payload, (bytes, bytearray))
        ):
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_value_not_serializable")
        return {
            _SERDE_VALUE_KEY: {
                "schema": _SERDE_VALUE_SCHEMA,
                "type": value_type,
                "data": base64.b64encode(bytes(payload)).decode("ascii"),
            }
        }

    def _decode_value(self, value: Any) -> Any:
        if _is_serde_envelope(value):
            envelope = value[_SERDE_VALUE_KEY]
            try:
                payload = base64.b64decode(
                    str(envelope["data"]).encode("ascii"),
                    validate=True,
                )
            except (KeyError, UnicodeEncodeError, ValueError) as exc:
                raise LangGraphCheckpointGatewayError("langgraph_checkpoint_serialized_value_invalid") from exc
            serde = getattr(self, "serde", None)
            loads_typed = getattr(serde, "loads_typed", None)
            if not callable(loads_typed):
                raise LangGraphCheckpointGatewayError("langgraph_checkpoint_serializer_unavailable")
            try:
                return loads_typed((str(envelope["type"]), payload))
            except Exception as exc:  # noqa: BLE001 - dependency boundary
                raise LangGraphCheckpointGatewayError("langgraph_checkpoint_serialized_value_invalid") from exc
        if isinstance(value, Mapping):
            return {str(key): self._decode_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._decode_value(item) for item in value]
        return value

    def delete_thread(self, thread_id: str) -> None:
        del thread_id
        raise LangGraphCheckpointGatewayError("langgraph_checkpoint_delete_forbidden")

    async def aget_tuple(self, config: Mapping[str, Any]) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: Mapping[str, Any] | None,
        *,
        filter: Mapping[str, Any] | None = None,
        before: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ):
        values = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for value in values:
            yield value

    async def aput(
        self,
        config: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        metadata: Mapping[str, Any],
        new_versions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: Mapping[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)


def binding_from_delegated_payload(*, task_id: str, payload: Mapping[str, Any]) -> LangGraphCheckpointBinding:
    if not str(task_id).strip():
        raise LangGraphCheckpointContractError("langgraph_checkpoint_binding_invalid")
    return LangGraphCheckpointBinding.from_mapping(
        {
            "tenant_id": payload.get("tenant_id"),
            "workflow_id": payload.get("workflow_id"),
            "run_id": payload.get("run_id"),
            "step_id": payload.get("step_id"),
            # Scope persistence to the authorization-bound step, never to a
            # caller-controlled worker task alias.
            "task_id": payload.get("step_id"),
            "plan_hash": payload.get("plan_hash"),
            "policy_version": payload.get("policy_version"),
            "fencing_token": payload.get("fencing_token"),
            "authorization_envelope": payload.get("authorization_envelope"),
        }
    )


def _is_serde_envelope(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {_SERDE_VALUE_KEY}:
        return False
    envelope = value.get(_SERDE_VALUE_KEY)
    return bool(
        isinstance(envelope, Mapping)
        and set(envelope) == {"schema", "type", "data"}
        and envelope.get("schema") == _SERDE_VALUE_SCHEMA
        and isinstance(envelope.get("type"), str)
        and isinstance(envelope.get("data"), str)
    )


def _validate_snapshot_binding(
    snapshot: LangGraphCheckpointSnapshot,
    binding: LangGraphCheckpointBinding,
) -> None:
    assert_langgraph_config_binding(snapshot.config, task_id=binding.task_id)
    if snapshot.parent_config is not None:
        assert_langgraph_config_binding(
            snapshot.parent_config,
            task_id=binding.task_id,
        )


def _base_config(task_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": str(task_id), "checkpoint_ns": ""}}


def _without_checkpoint_id(config: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(config)
    configurable = dict(value.get("configurable") or {})
    configurable.pop("checkpoint_id", None)
    configurable.pop("ananta_checkpoint_revision", None)
    value["configurable"] = configurable
    return value


def _http_error_reason(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(65_537)
        if len(raw) > 65_536:
            return ""
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    data = decoded.get("data") if isinstance(decoded, Mapping) else None
    return str(data.get("reason_code") or "") if isinstance(data, Mapping) else ""


__all__ = [
    "HttpLangGraphCheckpointGateway",
    "LangGraphCheckpointGatewayError",
    "LangGraphCheckpointGatewayPort",
    "LangGraphHubOwnedCheckpointer",
    "binding_from_delegated_payload",
]
