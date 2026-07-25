"""Hub-owned vector-store rollout resolution and persistence.

Only the Hub stores overrides. Workers receive an immutable, secret-free
resolved projection inside a delegated task envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROLLOUT_STATE_SCHEMA = "ananta.vector_store_rollout_state.v1"
RESOLVED_CONFIG_SCHEMA = "ananta.vector_store_resolved_config.v1"
_DOMAINS = frozenset({"codecompass", "wiki"})
_PROVIDERS = frozenset({"json", "qdrant"})
_LAYERS = frozenset({"profile", "workspace"})
_ALLOWED_OVERRIDE_FIELDS = frozenset(
    {"provider", "availability", "json", "qdrant"}
)
_SAFE_SECRET_REFERENCE_SUFFIXES = ("_ref", "_file", "_env")
_SECRET_MARKERS = ("api_key", "password", "secret", "token", "authorization")
_SCOPE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = _clone(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = _clone(value)
    return result


def _safe_scope_name(value: str, *, field: str) -> str:
    candidate = str(value or "").strip()
    if _SCOPE_NAME.fullmatch(candidate) is None:
        raise ValueError(f"vector_store_{field}_invalid")
    return candidate


def _safe_domain(value: str) -> str:
    domain = str(value or "codecompass").strip().lower()
    if domain not in _DOMAINS:
        raise ValueError("vector_store_domain_invalid")
    return domain


def _contains_plaintext_secret(value: Any, *, key: str = "") -> bool:
    normalized_key = str(key or "").strip().lower()
    if normalized_key and any(marker in normalized_key for marker in _SECRET_MARKERS):
        if not normalized_key.endswith(_SAFE_SECRET_REFERENCE_SUFFIXES):
            return True
    if isinstance(value, Mapping):
        return any(
            _contains_plaintext_secret(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_plaintext_secret(item) for item in value)
    return False


def validate_vector_store_override(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("vector_store_override_mapping_required")
    unknown = set(raw) - _ALLOWED_OVERRIDE_FIELDS
    if unknown:
        raise ValueError("vector_store_override_fields_forbidden")
    override = _clone(dict(raw))
    if _contains_plaintext_secret(override):
        raise ValueError("vector_store_plaintext_secret_forbidden")
    provider = override.get("provider")
    if provider is not None:
        normalized = str(provider or "").strip().lower()
        if normalized not in _PROVIDERS:
            raise ValueError("vector_store_provider_invalid")
        override["provider"] = normalized
    for field in ("availability", "json", "qdrant"):
        if field in override and not isinstance(override[field], Mapping):
            raise ValueError(f"vector_store_{field}_mapping_required")
    return override


@dataclass(frozen=True)
class VectorStoreOverrideRecord:
    key: str
    layer: str
    domain: str
    scope_name: str
    override: dict[str, Any]
    revision: int
    updated_by: str
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "layer": self.layer,
            "domain": self.domain,
            "scope_name": self.scope_name,
            "override": _clone(self.override),
            "revision": self.revision,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VectorStoreOverrideRecord":
        return cls(
            key=str(value.get("key") or ""),
            layer=str(value.get("layer") or ""),
            domain=str(value.get("domain") or ""),
            scope_name=str(value.get("scope_name") or ""),
            override=validate_vector_store_override(
                dict(value.get("override") or {})
            ),
            revision=int(value.get("revision") or 0),
            updated_by=str(value.get("updated_by") or ""),
            updated_at=float(value.get("updated_at") or 0.0),
        )


class VectorStoreRolloutStore(Protocol):
    def get(self, key: str) -> VectorStoreOverrideRecord | None: ...

    def put(self, record: VectorStoreOverrideRecord) -> None: ...

    def delete(self, key: str) -> None: ...


class InMemoryVectorStoreRolloutStore:
    def __init__(self) -> None:
        self._records: dict[str, VectorStoreOverrideRecord] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> VectorStoreOverrideRecord | None:
        with self._lock:
            record = self._records.get(str(key))
            return (
                VectorStoreOverrideRecord.from_mapping(record.to_dict())
                if record is not None
                else None
            )

    def put(self, record: VectorStoreOverrideRecord) -> None:
        with self._lock:
            self._records[record.key] = VectorStoreOverrideRecord.from_mapping(
                record.to_dict()
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._records.pop(str(key), None)


class JsonVectorStoreRolloutStore:
    """Atomic runtime persistence adapter for Hub-owned rollout state."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def get(self, key: str) -> VectorStoreOverrideRecord | None:
        with self._lock:
            payload = self._read()
            raw = dict(payload.get("records") or {}).get(str(key))
            return (
                VectorStoreOverrideRecord.from_mapping(raw)
                if isinstance(raw, Mapping)
                else None
            )

    def put(self, record: VectorStoreOverrideRecord) -> None:
        with self._lock:
            payload = self._read()
            records = dict(payload.get("records") or {})
            records[record.key] = record.to_dict()
            self._write({"schema": ROLLOUT_STATE_SCHEMA, "records": records})

    def delete(self, key: str) -> None:
        with self._lock:
            payload = self._read()
            records = dict(payload.get("records") or {})
            records.pop(str(key), None)
            self._write({"schema": ROLLOUT_STATE_SCHEMA, "records": records})

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": ROLLOUT_STATE_SCHEMA, "records": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != ROLLOUT_STATE_SCHEMA:
            raise ValueError("vector_store_rollout_state_invalid")
        if not isinstance(raw.get("records"), dict):
            raise ValueError("vector_store_rollout_records_invalid")
        return raw

    def _write(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_bytes(_canonical_json(payload) + b"\n")
        os.replace(temporary, self._path)


@dataclass(frozen=True)
class ResolvedVectorStoreConfig:
    domain: str
    workspace_id: str
    profile_name: str
    provider: str
    config_hash: str
    config: dict[str, Any]
    source_layers: tuple[str, ...]

    def to_worker_payload(self) -> dict[str, Any]:
        return {
            "schema": RESOLVED_CONFIG_SCHEMA,
            "provider": self.provider,
            "config_hash": self.config_hash,
            "config": _clone(self.config),
            "source_layers": list(self.source_layers),
        }


class VectorStoreRolloutService:
    """Resolve global JSON -> profile -> workspace with explicit opt-in."""

    def __init__(
        self,
        *,
        store: VectorStoreRolloutStore | None = None,
        global_config: Mapping[str, Any] | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store or JsonVectorStoreRolloutStore(
            Path(
                os.environ.get(
                    "ANANTA_VECTOR_STORE_ROLLOUT_STATE_PATH",
                    "data/vector-store-rollout.json",
                )
            )
        )
        candidate = _deep_merge(
            {
                "provider": "json",
                "availability": {
                    "on_unavailable": "degraded_empty",
                    "fallback_provider": None,
                },
                "json": {},
                "qdrant": {},
            },
            dict(global_config or {}),
        )
        if str(candidate.get("provider") or "") != "json":
            raise ValueError("vector_store_global_default_must_be_json")
        if _contains_plaintext_secret(candidate):
            raise ValueError("vector_store_plaintext_secret_forbidden")
        self._global_config = candidate
        self._audit = audit or self._default_audit
        if clock is None:
            import time

            clock = time.time
        self._clock = clock
        self._lock = threading.RLock()

    @staticmethod
    def _default_audit(event: str, payload: dict[str, Any]) -> None:
        from agent.common.audit import log_audit

        log_audit(event, payload)

    @staticmethod
    def _key(layer: str, domain: str, scope_name: str) -> str:
        return f"{layer}:{domain}:{scope_name}"

    def set_profile_override(
        self,
        *,
        domain: str,
        profile_name: str,
        override: Mapping[str, Any],
        expected_revision: int,
        actor: str,
    ) -> VectorStoreOverrideRecord:
        return self._set(
            layer="profile",
            domain=domain,
            scope_name=profile_name,
            override=override,
            expected_revision=expected_revision,
            actor=actor,
        )

    def set_workspace_override(
        self,
        *,
        domain: str,
        workspace_id: str,
        override: Mapping[str, Any],
        expected_revision: int,
        actor: str,
    ) -> VectorStoreOverrideRecord:
        return self._set(
            layer="workspace",
            domain=domain,
            scope_name=workspace_id,
            override=override,
            expected_revision=expected_revision,
            actor=actor,
        )

    def get_override(
        self,
        *,
        layer: str,
        domain: str,
        scope_name: str,
    ) -> VectorStoreOverrideRecord | None:
        normalized_layer = str(layer or "").strip().lower()
        if normalized_layer not in _LAYERS:
            raise ValueError("vector_store_rollout_layer_invalid")
        normalized_domain = _safe_domain(domain)
        normalized_scope = _safe_scope_name(scope_name, field=f"{normalized_layer}_id")
        return self._store.get(
            self._key(normalized_layer, normalized_domain, normalized_scope)
        )

    def rollback(
        self,
        *,
        layer: str,
        domain: str,
        scope_name: str,
        expected_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get_override(
                layer=layer,
                domain=domain,
                scope_name=scope_name,
            )
            if record is None:
                raise ValueError("vector_store_override_not_found")
            if record.revision != int(expected_revision):
                raise RuntimeError("vector_store_override_revision_conflict")
            self._store.delete(record.key)
            self._audit(
                "vector_store_override_rolled_back",
                {
                    "actor": str(actor or "unknown"),
                    "domain": record.domain,
                    "layer": record.layer,
                    "scope_hash": hashlib.sha256(
                        record.scope_name.encode("utf-8")
                    ).hexdigest(),
                    "previous_revision": record.revision,
                    "provider": str(record.override.get("provider") or "inherited"),
                },
            )
            return {
                "status": "rolled_back",
                "domain": record.domain,
                "layer": record.layer,
                "previous_revision": record.revision,
            }

    def resolve(
        self,
        *,
        domain: str,
        workspace_id: str,
        profile_name: str = "default",
    ) -> ResolvedVectorStoreConfig:
        normalized_domain = _safe_domain(domain)
        workspace = _safe_scope_name(workspace_id, field="workspace_id")
        profile = _safe_scope_name(profile_name, field="profile_name")
        config = _clone(self._global_config)
        source_layers = ["global_json_default"]
        profile_record = self._store.get(
            self._key("profile", normalized_domain, profile)
        )
        if profile_record is not None:
            config = _deep_merge(config, profile_record.override)
            source_layers.append("profile_override")
        workspace_record = self._store.get(
            self._key("workspace", normalized_domain, workspace)
        )
        if workspace_record is not None:
            config = _deep_merge(config, workspace_record.override)
            source_layers.append("workspace_override")
        provider = str(config.get("provider") or "json").strip().lower()
        if provider not in _PROVIDERS:
            raise ValueError("vector_store_provider_invalid")
        if provider == "qdrant" and len(source_layers) == 1:
            raise ValueError("vector_store_qdrant_opt_in_required")
        if _contains_plaintext_secret(config):
            raise ValueError("vector_store_plaintext_secret_forbidden")
        config_hash = hashlib.sha256(_canonical_json(config)).hexdigest()
        return ResolvedVectorStoreConfig(
            domain=normalized_domain,
            workspace_id=workspace,
            profile_name=profile,
            provider=provider,
            config_hash=config_hash,
            config=config,
            source_layers=tuple(source_layers),
        )

    def _set(
        self,
        *,
        layer: str,
        domain: str,
        scope_name: str,
        override: Mapping[str, Any],
        expected_revision: int,
        actor: str,
    ) -> VectorStoreOverrideRecord:
        normalized_layer = str(layer or "").strip().lower()
        if normalized_layer not in _LAYERS:
            raise ValueError("vector_store_rollout_layer_invalid")
        normalized_domain = _safe_domain(domain)
        normalized_scope = _safe_scope_name(
            scope_name,
            field=f"{normalized_layer}_id",
        )
        normalized_override = validate_vector_store_override(override)
        with self._lock:
            key = self._key(
                normalized_layer,
                normalized_domain,
                normalized_scope,
            )
            current = self._store.get(key)
            revision = current.revision if current is not None else 0
            if revision != int(expected_revision):
                raise RuntimeError("vector_store_override_revision_conflict")
            record = VectorStoreOverrideRecord(
                key=key,
                layer=normalized_layer,
                domain=normalized_domain,
                scope_name=normalized_scope,
                override=normalized_override,
                revision=revision + 1,
                updated_by=str(actor or "unknown"),
                updated_at=float(self._clock()),
            )
            self._store.put(record)
            self._audit(
                "vector_store_override_updated",
                {
                    "actor": record.updated_by,
                    "domain": record.domain,
                    "layer": record.layer,
                    "scope_hash": hashlib.sha256(
                        record.scope_name.encode("utf-8")
                    ).hexdigest(),
                    "revision": record.revision,
                    "provider": str(
                        record.override.get("provider") or "inherited"
                    ),
                    "override_hash": hashlib.sha256(
                        _canonical_json(record.override)
                    ).hexdigest(),
                },
            )
            return record


vector_store_rollout_service = VectorStoreRolloutService()


def get_vector_store_rollout_service() -> VectorStoreRolloutService:
    return vector_store_rollout_service


__all__ = [
    "InMemoryVectorStoreRolloutStore",
    "JsonVectorStoreRolloutStore",
    "RESOLVED_CONFIG_SCHEMA",
    "ROLLOUT_STATE_SCHEMA",
    "ResolvedVectorStoreConfig",
    "VectorStoreOverrideRecord",
    "VectorStoreRolloutService",
    "get_vector_store_rollout_service",
    "validate_vector_store_override",
]
