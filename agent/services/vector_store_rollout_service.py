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

from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreConfigError,
    VectorStoreProvider,
)

ROLLOUT_STATE_SCHEMA = "ananta.vector_store_rollout_state.v1"
RESOLVED_CONFIG_SCHEMA = "ananta.vector_store_resolved_config.v1"
_DOMAINS = frozenset({"codecompass", "wiki"})
_PROVIDERS = frozenset({"json", "qdrant", "duckdb"})
_LAYERS = frozenset({"profile", "workspace"})
_ALLOWED_OVERRIDE_FIELDS = frozenset(
    {"provider", "availability", "json", "qdrant", "duckdb"}
)
_SAFE_SECRET_REFERENCE_SUFFIXES = ("_ref", "_file", "_env")
_SECRET_MARKERS = ("api_key", "password", "secret", "token", "authorization")
_SCOPE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IMMUTABLE_QDRANT_ENDPOINT_FIELDS = frozenset(
    {
        "allowed_origins",
        "allowed_base_urls",
        "trusted_private_origins",
        "external_calls_allowed",
        "tls_verify",
        "api_key_ref",
        "api_key_env",
        "api_key",
        "tls_ca_cert_ref",
    }
)
_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


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


def _without_secret_references(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    if normalized_key in {
        "api_key_ref",
        "api_key_env",
        "tls_ca_cert_ref",
    }:
        return None
    if isinstance(value, Mapping):
        return {
            str(item_key): _without_secret_references(
                item,
                key=str(item_key),
            )
            for item_key, item in value.items()
            if str(item_key).strip().lower()
            not in {
                "api_key_ref",
                "api_key_env",
                "tls_ca_cert_ref",
            }
        }
    if isinstance(value, list):
        return [_without_secret_references(item) for item in value]
    return value


def _reject_security_policy_override(override: Mapping[str, Any]) -> None:
    json_config = override.get("json")
    if isinstance(json_config, Mapping) and "index_path" in json_config:
        raise ValueError("vector_store_json_index_path_override_forbidden")
    qdrant = override.get("qdrant")
    if not isinstance(qdrant, Mapping):
        return
    if set(qdrant) & _IMMUTABLE_QDRANT_ENDPOINT_FIELDS:
        raise ValueError("vector_store_security_policy_override_forbidden")
    endpoint = qdrant.get("endpoint")
    if endpoint is not None and not isinstance(endpoint, Mapping):
        raise ValueError("vector_store_qdrant_endpoint_mapping_required")
    if isinstance(endpoint, Mapping) and (
        set(endpoint) & _IMMUTABLE_QDRANT_ENDPOINT_FIELDS
    ):
        raise ValueError("vector_store_security_policy_override_forbidden")


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
    _reject_security_policy_override(override)
    return override


class VectorStoreGlobalEnvConfigLoader:
    """Build the immutable Hub policy without resolving worker secrets."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ

    def load(self) -> dict[str, Any]:
        rest_url = self._text(
            "ANANTA_QDRANT_REST_URL",
            default="http://localhost:6333",
        )
        grpc_url = self._text("ANANTA_QDRANT_GRPC_URL")
        allowed_origins = self._origins(
            "ANANTA_QDRANT_ALLOWED_ORIGINS",
            default=(rest_url, *((grpc_url,) if grpc_url else ())),
        )
        trusted_private_origins = self._origins(
            "ANANTA_QDRANT_TRUSTED_PRIVATE_ORIGINS",
            default=(),
        )
        endpoint: dict[str, Any] = {
            "rest_url": rest_url,
            "allowed_origins": allowed_origins,
            "trusted_private_origins": trusted_private_origins,
            "external_calls_allowed": self._boolean(
                "ANANTA_QDRANT_EXTERNAL_CALLS_ALLOWED",
                default=False,
            ),
            "connect_timeout_seconds": self._number(
                "ANANTA_QDRANT_CONNECT_TIMEOUT_SECONDS",
                default=3.0,
            ),
            "request_timeout_seconds": self._number(
                "ANANTA_QDRANT_REQUEST_TIMEOUT_SECONDS",
                default=10.0,
            ),
            "prefer_grpc": self._boolean(
                "ANANTA_QDRANT_PREFER_GRPC",
                default=False,
            ),
            "tls_verify": True,
        }
        if grpc_url:
            endpoint["grpc_url"] = grpc_url
        api_key_ref = self._text("ANANTA_QDRANT_API_KEY_REF")
        if api_key_ref:
            endpoint["api_key_ref"] = api_key_ref
        tls_ca_cert_ref = self._text("ANANTA_QDRANT_TLS_CA_CERT_REF")
        if tls_ca_cert_ref:
            endpoint["tls_ca_cert_ref"] = tls_ca_cert_ref
        return {
            "provider": "json",
            "availability": {
                "on_unavailable": "degraded_empty",
                "fallback_provider": None,
            },
            "json": {},
            "qdrant": {"endpoint": endpoint},
        }

    def _text(self, name: str, *, default: str = "") -> str:
        return str(self._environ.get(name, default) or "").strip()

    def _origins(
        self,
        name: str,
        *,
        default: tuple[str, ...],
    ) -> list[str]:
        raw = self._text(name)
        if not raw:
            return [item for item in default if item]
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if not values:
            raise ValueError("vector_store_environment_origins_invalid")
        return values

    def _boolean(self, name: str, *, default: bool) -> bool:
        raw = self._text(name)
        if not raw:
            return default
        normalized = raw.lower()
        if normalized in _ENV_TRUE_VALUES:
            return True
        if normalized in _ENV_FALSE_VALUES:
            return False
        raise ValueError("vector_store_environment_boolean_invalid")

    def _number(self, name: str, *, default: float) -> float:
        raw = self._text(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError("vector_store_environment_number_invalid") from exc


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

    def list_records(
        self,
        *,
        layer: str,
        domain: str,
    ) -> tuple[VectorStoreOverrideRecord, ...]: ...

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

    def list_records(
        self,
        *,
        layer: str,
        domain: str,
    ) -> tuple[VectorStoreOverrideRecord, ...]:
        with self._lock:
            return tuple(
                VectorStoreOverrideRecord.from_mapping(record.to_dict())
                for _, record in sorted(self._records.items())
                if record.layer == layer and record.domain == domain
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

    def list_records(
        self,
        *,
        layer: str,
        domain: str,
    ) -> tuple[VectorStoreOverrideRecord, ...]:
        with self._lock:
            payload = self._read()
            records = dict(payload.get("records") or {})
            selected = []
            for key, raw in sorted(records.items()):
                if not isinstance(raw, Mapping):
                    continue
                record = VectorStoreOverrideRecord.from_mapping(raw)
                if record.layer == layer and record.domain == domain:
                    selected.append(record)
            return tuple(selected)

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
        parsed_global = self._parse_production_config(candidate)
        if parsed_global.provider != VectorStoreProvider.JSON:
            raise ValueError("vector_store_global_default_must_be_json")
        self._global_config = parsed_global.as_dict()
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
            previous_provider = self._effective_provider_for_audit(
                layer=record.layer,
                domain=record.domain,
                target_override=record.override,
            )
            new_provider = self._effective_provider_for_audit(
                layer=record.layer,
                domain=record.domain,
                target_override=None,
            )
            self._store.delete(record.key)
            occurred_at = float(self._clock())
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
                    "provider": new_provider,
                    "previous_provider": previous_provider,
                    "new_provider": new_provider,
                    "policy_decision": "rollback_allowed",
                    "occurred_at": occurred_at,
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
        config = self._apply_domain_config(
            normalized_domain,
            config,
        )
        parsed = self._parse_production_config(config)
        provider = parsed.provider.value
        if provider == "qdrant" and len(source_layers) == 1:
            raise ValueError("vector_store_qdrant_opt_in_required")
        normalized_config = parsed.as_dict()
        config_hash = hashlib.sha256(
            _canonical_json(
                _without_secret_references(normalized_config)
            )
        ).hexdigest()
        return ResolvedVectorStoreConfig(
            domain=normalized_domain,
            workspace_id=workspace,
            profile_name=profile,
            provider=provider,
            config_hash=config_hash,
            config=normalized_config,
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
            self._validate_candidate_override(
                normalized_override,
                layer=normalized_layer,
                domain=normalized_domain,
            )
            previous_provider = self._effective_provider_for_audit(
                layer=normalized_layer,
                domain=normalized_domain,
                target_override=(
                    current.override if current is not None else None
                ),
            )
            new_provider = self._effective_provider_for_audit(
                layer=normalized_layer,
                domain=normalized_domain,
                target_override=normalized_override,
            )
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
                    "provider": new_provider,
                    "previous_provider": previous_provider,
                    "new_provider": new_provider,
                    "policy_decision": "override_allowed",
                    "occurred_at": record.updated_at,
                    "override_hash": hashlib.sha256(
                        _canonical_json(record.override)
                    ).hexdigest(),
                },
            )
            return record

    def _effective_provider_for_audit(
        self,
        *,
        layer: str,
        domain: str,
        target_override: Mapping[str, Any] | None,
    ) -> str:
        providers = {
            self._parse_production_config(candidate).provider.value
            for candidate in self._candidate_layer_configs(
                layer=layer,
                domain=domain,
                target_override=target_override,
            )
        }
        if len(providers) == 1:
            return next(iter(providers))
        return "mixed"

    @staticmethod
    def _parse_production_config(
        value: Mapping[str, Any],
    ) -> VectorStoreConfig:
        try:
            return VectorStoreConfig.from_mapping(value)
        except VectorStoreConfigError as exc:
            raise ValueError(exc.reason) from exc

    def _validate_candidate_override(
        self,
        override: Mapping[str, Any],
        *,
        layer: str,
        domain: str,
    ) -> None:
        for candidate in self._candidate_layer_configs(
            layer=layer,
            domain=domain,
            target_override=override,
        ):
            self._parse_production_config(candidate)

    def _candidate_layer_configs(
        self,
        *,
        layer: str,
        domain: str,
        target_override: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        opposite_layer = (
            "workspace" if layer == "profile" else "profile"
        )
        opposite_records = self._store.list_records(
            layer=opposite_layer,
            domain=domain,
        )
        combinations: list[tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]]
        if layer == "profile":
            combinations = [(target_override, None)]
            combinations.extend(
                (target_override, record.override)
                for record in opposite_records
            )
        else:
            combinations = [(None, target_override)]
            combinations.extend(
                (record.override, target_override)
                for record in opposite_records
            )
        candidates: list[dict[str, Any]] = []
        for profile_override, workspace_override in combinations:
            config = _clone(self._global_config)
            if profile_override is not None:
                config = _deep_merge(config, profile_override)
            if workspace_override is not None:
                config = _deep_merge(config, workspace_override)
            candidates.append(
                self._apply_domain_config(domain, config)
            )
        return tuple(candidates)

    @staticmethod
    def _apply_domain_config(
        domain: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep Wiki collections physically distinct from other domains."""

        config = _clone(dict(value))
        if domain != "wiki":
            return config
        qdrant = dict(config.get("qdrant") or {})
        prefix = str(
            qdrant.get("collection_prefix") or "ananta"
        ).strip()
        if prefix == "ananta":
            qdrant["collection_prefix"] = "ananta-wiki"
        elif not prefix.startswith("ananta-wiki"):
            raise ValueError(
                "wiki_qdrant_collection_prefix_must_be_separate"
            )
        config["qdrant"] = qdrant
        return config


vector_store_rollout_service = VectorStoreRolloutService(
    global_config=VectorStoreGlobalEnvConfigLoader().load()
)


def get_vector_store_rollout_service() -> VectorStoreRolloutService:
    return vector_store_rollout_service


__all__ = [
    "InMemoryVectorStoreRolloutStore",
    "JsonVectorStoreRolloutStore",
    "RESOLVED_CONFIG_SCHEMA",
    "ROLLOUT_STATE_SCHEMA",
    "ResolvedVectorStoreConfig",
    "VectorStoreOverrideRecord",
    "VectorStoreGlobalEnvConfigLoader",
    "VectorStoreRolloutService",
    "get_vector_store_rollout_service",
    "validate_vector_store_override",
]
