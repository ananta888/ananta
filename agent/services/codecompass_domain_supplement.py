"""Read-only access to revision-bound CodeCompass domain supplements.

This module stays the public entry point. Collaborators:

* ``codecompass_domain_supplement_models`` -- value types, errors, limits
* ``codecompass_domain_supplement_sqlite_session`` -- immutable SQLite session
* ``codecompass_domain_supplement_sqlite_schema`` -- schema/metadata checks
* ``codecompass_domain_supplement_chunk_codec`` -- chunk decode and JSONL
* ``codecompass_domain_supplement_domain_cache`` -- bounded domain cache
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent.services.artifact_integrity_verifier import (
    ArtifactIntegrityVerifierPort,
    get_artifact_integrity_verifier,
)
from agent.services.codecompass_domain_supplement_chunk_codec import (
    decode_domain_supplement_chunk,
    domain_supplement_chunk_logical_line,
    domain_supplement_domain_logical_line,
    iter_domain_supplement_chunk_rows,
    parse_domain_supplement_records,
)
from agent.services.codecompass_domain_supplement_domain_cache import (
    CodeCompassDomainSupplementDomainCache,
)
from agent.services.codecompass_domain_supplement_domain_stream import (
    DomainSupplementDomainRecords,
    assert_domain_supplement_stream_counts,
    load_domain_supplement_domain,
)
from agent.services.codecompass_domain_supplement_models import (
    DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN,
    DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET,
    CodeCompassDomainSupplementBinding,
    CodeCompassDomainSupplementCatalog,
    CodeCompassDomainSupplementError,
    CodeCompassDomainSupplementPort,
    CodeCompassDomainSupplementRecords,
    CodeCompassDomainSupplementSummary,
    invoke_domain_supplement_checkpoint,
)
from agent.services.codecompass_domain_supplement_sqlite_schema import (
    domain_supplement_metadata_count,
    parse_domain_supplement_summary,
    read_domain_supplement_metadata,
    validate_domain_supplement_metadata,
    validate_domain_supplement_schema,
)
from agent.services.codecompass_domain_supplement_sqlite_session import (
    open_domain_supplement_connection,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_PAYLOAD_KINDS,
    DOMAIN_SUPPLEMENT_SCHEMA,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)

# Compatibility re-exports: names that were importable from this module
# before its collaborators were extracted.
from agent.services.codecompass_domain_supplement_chunk_codec import (  # noqa: E402,F401,I001
    _MAX_COMPRESSED_CHUNK_BYTES,
    _MAX_IDENTIFIER_CHARACTERS,
    _canonical_json,
)
from agent.services.codecompass_domain_supplement_domain_cache import (  # noqa: E402,F401
    _CachedDomain,
)
from agent.services.codecompass_domain_supplement_sqlite_schema import (  # noqa: E402,F401
    _EXPECTED_SCHEMA_SQL,
    _SHA256,
    _TABLES,
    _normalized_sql,
)
from agent.services.codecompass_domain_supplement_sqlite_session import (  # noqa: E402,F401
    _SQLITE_PROGRESS_OPCODES,
    sqlite_checkpoint_progress as _sqlite_checkpoint_progress,
)
from ananta_contracts.codecompass_domain_supplement import (  # noqa: E402,F401
    DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID,
    DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION,
    codecompass_domain_supplement_canonical_json_bytes,
    codecompass_domain_supplement_decode_metadata,
    codecompass_domain_supplement_logical_chunk_header,
    codecompass_domain_supplement_logical_domain,
)
from ananta_contracts.codecompass_graph_limits import (  # noqa: E402,F401
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (  # noqa: E402,F401
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)

_DOMAIN_KEY = DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN
_MAX_VALIDATION_RAW_BYTES = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES
_MAX_SELECTED_RAW_BYTES = _MAX_VALIDATION_RAW_BYTES
_DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
_PAYLOAD_KINDS = DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET
_checkpoint = invoke_domain_supplement_checkpoint


class SqliteCodeCompassDomainSupplementReader:
    """Validate and lazily read immutable SQLite domain shards.

    SQLite is opened in immutable read-only mode. Schema, metadata, row counts,
    decompressed byte budgets, hashes and canonical JSONL are all checked before
    records are returned to the graph read path.
    """

    def __init__(
        self,
        *,
        integrity: ArtifactIntegrityVerifierPort | None = None,
        maximum_cached_domains: int = 4,
        maximum_cached_bytes: int = _DEFAULT_CACHE_BYTES,
        domain_cache: CodeCompassDomainSupplementDomainCache | None = None,
    ) -> None:
        if maximum_cached_domains < 1:
            raise ValueError("domain_supplement_cache_size_invalid")
        if not 1 <= maximum_cached_bytes <= MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError("domain_supplement_cache_byte_limit_invalid")
        self._integrity = integrity or get_artifact_integrity_verifier()
        self._domain_cache = domain_cache or CodeCompassDomainSupplementDomainCache(
            maximum_cached_domains=maximum_cached_domains,
            maximum_cached_bytes=maximum_cached_bytes,
        )

    _summary = staticmethod(parse_domain_supplement_summary)

    def validate_artifact(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog:
        _checkpoint(checkpoint)
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with open_domain_supplement_connection(path, checkpoint=checkpoint) as connection:
            catalog, metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            logical_hash = hashlib.sha256(DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX)
            total_raw_bytes = 0
            summaries = {item.domain_key: item for item in catalog.domains}
            observed: dict[str, dict[str, list[int]]] = {
                key: {kind: [0, 0, 0] for kind in _PAYLOAD_KINDS} for key in summaries
            }
            for summary in catalog.domains:
                _checkpoint(checkpoint)
                logical_hash.update(domain_supplement_domain_logical_line(summary))
                for payload_kind in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS:
                    for row in iter_domain_supplement_chunk_rows(
                        connection,
                        domain_keys=(summary.domain_key,),
                        payload_kind=payload_kind,
                    ):
                        _checkpoint(checkpoint)
                        domain_key, kind, ordinal, row_count, raw = decode_domain_supplement_chunk(row)
                        counters = observed[domain_key][kind]
                        if ordinal != counters[0]:
                            raise CodeCompassDomainSupplementError("domain_supplement_chunk_ordinal_invalid")
                        counters[0] += 1
                        counters[1] += row_count
                        counters[2] += len(raw)
                        total_raw_bytes += len(raw)
                        if total_raw_bytes > _MAX_VALIDATION_RAW_BYTES:
                            raise CodeCompassDomainSupplementError("domain_supplement_raw_budget_exceeded")
                        parse_domain_supplement_records(
                            raw=raw,
                            row_count=row_count,
                            payload_kind=kind,
                            domain_key=domain_key,
                        )
                        logical_hash.update(domain_supplement_chunk_logical_line(row))
                        logical_hash.update(raw)
            _checkpoint(checkpoint)
            self._assert_stream_counts(
                catalog=catalog,
                observed=observed,
            )
            actual_logical_hash = f"sha256:{logical_hash.hexdigest()}"
            if (
                actual_logical_hash != binding.logical_content_hash
                or actual_logical_hash != metadata["logical_content_hash"]
            ):
                raise CodeCompassDomainSupplementError("domain_supplement_logical_hash_mismatch")
            return catalog

    def catalog(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog:
        _checkpoint(checkpoint)
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with open_domain_supplement_connection(path, checkpoint=checkpoint) as connection:
            catalog, _metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            return catalog

    def load_domains(
        self,
        *,
        path: Path,
        domain_keys: Sequence[str],
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementRecords:
        _checkpoint(checkpoint)
        requested = tuple(sorted(set(domain_keys)))
        if not requested or any(_DOMAIN_KEY.fullmatch(key) is None for key in requested):
            raise CodeCompassDomainSupplementError("domain_supplement_selector_invalid")
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with open_domain_supplement_connection(path, checkpoint=checkpoint) as connection:
            catalog, _metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            summaries = {item.domain_key: item for item in catalog.domains}
            selected = [summaries[key] for key in requested if key in summaries]
            if not selected:
                return CodeCompassDomainSupplementRecords(
                    graph_revision=catalog.graph_revision,
                    logical_content_hash=catalog.logical_content_hash,
                    domain_keys=(),
                    nodes=(),
                    semantic_edges=(),
                    declaration_edges=(),
                    semantic_node_count=0,
                    semantic_edge_count=0,
                    declaration_edge_count=0,
                )

            nodes: list[Mapping[str, object]] = []
            semantic_edges: list[Mapping[str, object]] = []
            declaration_edges: list[Mapping[str, object]] = []
            expected_selected_raw_bytes = sum(
                item.semantic_node_bytes + item.semantic_edge_bytes + item.declaration_edge_bytes for item in selected
            )
            if expected_selected_raw_bytes > _MAX_SELECTED_RAW_BYTES:
                raise CodeCompassDomainSupplementError("domain_supplement_selected_budget_exceeded")
            for summary in selected:
                _checkpoint(checkpoint)
                cached = self._domain_cache.get(
                    binding=binding,
                    domain_key=summary.domain_key,
                )
                if cached is None:
                    records, raw_bytes = self._load_domain(
                        connection=connection,
                        summary=summary,
                        checkpoint=checkpoint,
                    )
                    self._domain_cache.put(
                        binding=binding,
                        domain_key=summary.domain_key,
                        records=records,
                        raw_size=raw_bytes,
                    )
                else:
                    records = cached.records
                domain_nodes, domain_semantic, domain_declarations = records
                nodes.extend(domain_nodes)
                semantic_edges.extend(domain_semantic)
                declaration_edges.extend(domain_declarations)
            _checkpoint(checkpoint)
            return CodeCompassDomainSupplementRecords(
                graph_revision=catalog.graph_revision,
                logical_content_hash=catalog.logical_content_hash,
                domain_keys=tuple(item.domain_key for item in selected),
                nodes=tuple(nodes),
                semantic_edges=tuple(semantic_edges),
                declaration_edges=tuple(declaration_edges),
                semantic_node_count=sum(item.semantic_node_count for item in selected),
                semantic_edge_count=sum(item.semantic_edge_count for item in selected),
                declaration_edge_count=sum(item.declaration_edge_count for item in selected),
            )

    def _load_domain(
        self,
        *,
        connection: sqlite3.Connection,
        summary: CodeCompassDomainSupplementSummary,
        checkpoint: Callable[[], object] | None = None,
    ) -> tuple[DomainSupplementDomainRecords, int]:
        return load_domain_supplement_domain(
            connection=connection,
            summary=summary,
            maximum_selected_raw_bytes=_MAX_SELECTED_RAW_BYTES,
            checkpoint=checkpoint,
        )

    _assert_stream_counts = staticmethod(assert_domain_supplement_stream_counts)

    def _verify_file(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> None:
        if path.name != DOMAIN_SUPPLEMENT_FILENAME:
            raise CodeCompassDomainSupplementError("domain_supplement_path_invalid")
        try:
            options = (
                {"checkpoint": checkpoint}
                if checkpoint is not None
                else {}
            )
            self._integrity.verify(
                path=path,
                expected_sha256=binding.artifact_sha256,
                maximum_bytes=MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
                **options,
            )
        except (OSError, ValueError) as exc:
            raise CodeCompassDomainSupplementError("domain_supplement_integrity_invalid") from exc

    def _validated_catalog(
        self,
        *,
        connection: sqlite3.Connection,
        binding: CodeCompassDomainSupplementBinding,
    ) -> tuple[CodeCompassDomainSupplementCatalog, dict[str, object]]:
        validate_domain_supplement_schema(connection)
        metadata = read_domain_supplement_metadata(connection)
        validate_domain_supplement_metadata(metadata=metadata, binding=binding)
        summaries = tuple(
            parse_domain_supplement_summary(row)
            for row in connection.execute(
                "SELECT domain_key,domain_kind,source_file_count,"
                "domain_label,"
                "semantic_node_count,semantic_edge_count,"
                "declaration_edge_count,semantic_node_bytes,"
                "semantic_edge_bytes,declaration_edge_bytes,complete "
                "FROM domains ORDER BY domain_key"
            )
        )
        if len(summaries) != domain_supplement_metadata_count(metadata, "domain_count"):
            raise CodeCompassDomainSupplementError("domain_supplement_domain_count_mismatch")
        if len(summaries) > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS:
            raise CodeCompassDomainSupplementError("domain_supplement_domain_limit_exceeded")
        expected_totals = {
            "semantic_node_count": sum(item.semantic_node_count for item in summaries),
            "semantic_edge_count": sum(item.semantic_edge_count for item in summaries),
            "declaration_edge_count": sum(item.declaration_edge_count for item in summaries),
        }
        if any(domain_supplement_metadata_count(metadata, key) != value for key, value in expected_totals.items()):
            raise CodeCompassDomainSupplementError("domain_supplement_record_count_mismatch")
        return (
            CodeCompassDomainSupplementCatalog(
                graph_revision=metadata["graph_revision"],
                logical_content_hash=metadata["logical_content_hash"],
                domains=summaries,
            ),
            metadata,
        )


codecompass_domain_supplement_reader = SqliteCodeCompassDomainSupplementReader()


def get_codecompass_domain_supplement_reader() -> SqliteCodeCompassDomainSupplementReader:
    return codecompass_domain_supplement_reader


__all__ = [
    "CodeCompassDomainSupplementBinding",
    "CodeCompassDomainSupplementCatalog",
    "CodeCompassDomainSupplementError",
    "CodeCompassDomainSupplementPort",
    "CodeCompassDomainSupplementRecords",
    "CodeCompassDomainSupplementSummary",
    "DOMAIN_SUPPLEMENT_FILENAME",
    "DOMAIN_SUPPLEMENT_MEDIA_TYPE",
    "DOMAIN_SUPPLEMENT_SCHEMA",
    "SqliteCodeCompassDomainSupplementReader",
    "get_codecompass_domain_supplement_reader",
]
