"""Strict DuckDB snapshot configuration. No network, no free SQL."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from worker.retrieval.vector_store_config import VectorStoreConfigError, _reject_unknown, _strict_bool

SCHEMA_VERSION = "ananta.codecompass_duckdb.v1"
ALLOWED_EXTENSIONS = frozenset({"parquet", "fts", "vss"})
VECTOR_MODES = frozenset({"exact"})


def _strict_int(value: Any, *, lo: int, hi: int, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VectorStoreConfigError("vector_store_invalid_integer", cause_reason=reason)
    if not lo <= value <= hi:
        raise VectorStoreConfigError("vector_store_invalid_integer", cause_reason=reason)
    return value


def _strict_text(value: Any, *, reason: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or "\x00" in value:
        raise VectorStoreConfigError("vector_store_invalid_text", cause_reason=reason)
    return value.strip()


@dataclass(frozen=True, slots=True)
class DuckDBExtensionPolicyConfig:
    allowed: tuple[str, ...] = ("parquet",)
    autoinstall_known_extensions: bool = False
    autoload_known_extensions: bool = False
    allow_community_extensions: bool = False
    allow_unsigned_extensions: bool = False
    offline_bundle_required: bool = True

    def __post_init__(self) -> None:
        allowed = tuple(str(item).strip().lower() for item in self.allowed)
        unknown = sorted(set(allowed) - ALLOWED_EXTENSIONS)
        if unknown:
            raise VectorStoreConfigError(
                "duckdb_extension_not_allowlisted",
                cause_reason=",".join(unknown),
            )
        if self.autoinstall_known_extensions or self.autoload_known_extensions:
            raise VectorStoreConfigError(
                "duckdb_extension_autoinstall_forbidden",
                cause_reason="autoinstall_or_autoload_enabled",
            )
        if self.allow_community_extensions or self.allow_unsigned_extensions:
            raise VectorStoreConfigError(
                "duckdb_unsigned_extensions_forbidden",
                cause_reason="community_or_unsigned_enabled",
            )
        object.__setattr__(self, "allowed", allowed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": list(self.allowed),
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_community_extensions": False,
            "allow_unsigned_extensions": False,
            "offline_bundle_required": bool(self.offline_bundle_required),
        }


@dataclass(frozen=True, slots=True)
class DuckDBResourceConfig:
    threads: int = 4
    memory_limit: str = "2GB"
    max_temp_directory_size: str = "4GB"
    temp_directory: Path = Path(".rag/codecompass/duckdb/tmp")
    query_timeout_ms: int = 5000
    max_result_rows: int = 1000
    max_import_bytes: int = 2_147_483_648

    def __post_init__(self) -> None:
        object.__setattr__(self, "threads", _strict_int(self.threads, lo=1, hi=32, reason="duckdb_threads"))
        object.__setattr__(
            self,
            "query_timeout_ms",
            _strict_int(self.query_timeout_ms, lo=50, hi=60_000, reason="duckdb_query_timeout"),
        )
        object.__setattr__(
            self,
            "max_result_rows",
            _strict_int(self.max_result_rows, lo=1, hi=10_000, reason="duckdb_max_result_rows"),
        )
        object.__setattr__(
            self,
            "max_import_bytes",
            _strict_int(self.max_import_bytes, lo=1024, hi=20_000_000_000, reason="duckdb_max_import_bytes"),
        )
        object.__setattr__(self, "temp_directory", Path(str(self.temp_directory)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "threads": self.threads,
            "memory_limit": self.memory_limit,
            "max_temp_directory_size": self.max_temp_directory_size,
            "temp_directory": str(self.temp_directory),
            "query_timeout_ms": self.query_timeout_ms,
            "max_result_rows": self.max_result_rows,
            "max_import_bytes": self.max_import_bytes,
        }


@dataclass(frozen=True, slots=True)
class DuckDBVectorStoreConfig:
    snapshot_root: Path = Path(".rag/codecompass/duckdb")
    active_pointer_name: str = "active-snapshot.json"
    schema_version: str = SCHEMA_VERSION
    scope_strategy: str = "workspace_repository_profile"
    access_mode: str = "worker_single_writer_snapshot"
    distance: str = "cosine"
    retention_snapshots: int = 2
    vector_search_mode: str = "exact"
    vss_enabled: bool = False
    fts_enabled: bool = False
    free_form_sql: bool = False
    network_access: bool = False
    allow_attach: bool = False
    extensions: DuckDBExtensionPolicyConfig = field(default_factory=DuckDBExtensionPolicyConfig)
    resources: DuckDBResourceConfig = field(default_factory=DuckDBResourceConfig)

    def __post_init__(self) -> None:
        root = Path(str(self.snapshot_root or "").strip() or ".rag/codecompass/duckdb")
        if "\x00" in str(root):
            raise VectorStoreConfigError("invalid_duckdb_snapshot_root")
        if self.vector_search_mode not in VECTOR_MODES:
            raise VectorStoreConfigError(
                "duckdb_vector_mode_unsupported",
                cause_reason=str(self.vector_search_mode),
            )
        if self.vss_enabled:
            raise VectorStoreConfigError(
                "duckdb_vss_requires_experimental_profile",
                cause_reason="vss_default_forbidden",
            )
        if self.free_form_sql or self.network_access or self.allow_attach:
            raise VectorStoreConfigError(
                "duckdb_security_policy_violation",
                cause_reason="free_sql_network_or_attach",
            )
        object.__setattr__(self, "snapshot_root", root)
        object.__setattr__(self, "active_pointer_name", _strict_text(self.active_pointer_name, reason="pointer", default="active-snapshot.json"))
        object.__setattr__(self, "schema_version", _strict_text(self.schema_version, reason="schema", default=SCHEMA_VERSION))
        object.__setattr__(self, "retention_snapshots", _strict_int(self.retention_snapshots, lo=1, hi=10, reason="retention"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_root": str(self.snapshot_root),
            "active_pointer_name": self.active_pointer_name,
            "schema_version": self.schema_version,
            "scope_strategy": self.scope_strategy,
            "access_mode": self.access_mode,
            "distance": self.distance,
            "retention_snapshots": self.retention_snapshots,
            "vector_search": {"mode": self.vector_search_mode, "vss": {"enabled": False}},
            "fts": {"enabled": bool(self.fts_enabled)},
            "extensions": self.extensions.as_dict(),
            "resources": self.resources.as_dict(),
            "security": {
                "free_form_sql": False,
                "network_access": False,
                "allow_attach": False,
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "DuckDBVectorStoreConfig":
        payload = dict(value or {})
        _reject_unknown(
            payload,
            {
                "snapshot_root",
                "active_pointer_name",
                "schema_version",
                "scope_strategy",
                "access_mode",
                "distance",
                "retention_snapshots",
                "vector_search",
                "fts",
                "extensions",
                "resources",
                "security",
            },
            "unknown_duckdb_vector_store_config_fields",
        )
        vector_search = dict(payload.get("vector_search") or {})
        vss = dict(vector_search.get("vss") or {})
        fts = dict(payload.get("fts") or {})
        security = dict(payload.get("security") or {})
        extensions = dict(payload.get("extensions") or {})
        resources = dict(payload.get("resources") or {})
        return cls(
            snapshot_root=Path(payload.get("snapshot_root") or ".rag/codecompass/duckdb"),
            active_pointer_name=str(payload.get("active_pointer_name") or "active-snapshot.json"),
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
            scope_strategy=str(payload.get("scope_strategy") or "workspace_repository_profile"),
            access_mode=str(payload.get("access_mode") or "worker_single_writer_snapshot"),
            distance=str(payload.get("distance") or "cosine"),
            retention_snapshots=int(payload.get("retention_snapshots") or 2),
            vector_search_mode=str(vector_search.get("mode") or "exact"),
            vss_enabled=_strict_bool(vss.get("enabled", False), cause_reason="duckdb_vss_enabled"),
            fts_enabled=_strict_bool(fts.get("enabled", False), cause_reason="duckdb_fts_enabled"),
            free_form_sql=_strict_bool(security.get("free_form_sql", False), cause_reason="duckdb_free_form_sql"),
            network_access=_strict_bool(security.get("network_access", False), cause_reason="duckdb_network_access"),
            allow_attach=_strict_bool(security.get("allow_attach", False), cause_reason="duckdb_allow_attach"),
            extensions=DuckDBExtensionPolicyConfig(
                allowed=tuple(extensions.get("allowed") or ("parquet",)),
                autoinstall_known_extensions=_strict_bool(
                    extensions.get("autoinstall_known_extensions", False),
                    cause_reason="duckdb_autoinstall",
                ),
                autoload_known_extensions=_strict_bool(
                    extensions.get("autoload_known_extensions", False),
                    cause_reason="duckdb_autoload",
                ),
                allow_community_extensions=_strict_bool(
                    extensions.get("allow_community_extensions", False),
                    cause_reason="duckdb_community",
                ),
                allow_unsigned_extensions=_strict_bool(
                    extensions.get("allow_unsigned_extensions", False),
                    cause_reason="duckdb_unsigned",
                ),
                offline_bundle_required=_strict_bool(
                    extensions.get("offline_bundle_required", True),
                    cause_reason="duckdb_offline_bundle",
                ),
            ),
            resources=DuckDBResourceConfig(
                threads=int(resources.get("threads") or 4),
                memory_limit=str(resources.get("memory_limit") or "2GB"),
                max_temp_directory_size=str(resources.get("max_temp_directory_size") or "4GB"),
                temp_directory=Path(resources.get("temp_directory") or ".rag/codecompass/duckdb/tmp"),
                query_timeout_ms=int(resources.get("query_timeout_ms") or 5000),
                max_result_rows=int(resources.get("max_result_rows") or 1000),
                max_import_bytes=int(resources.get("max_import_bytes") or 2_147_483_648),
            ),
        )
