"""Deterministic CodeCompass graph outputs for governed repository records.

Domain partitioning/admission evidence and the budgeted semantic collectors
are composed from ``repository_codecompass_domain_admission`` and
``repository_codecompass_semantic_budget``; they are re-exported here for
existing importers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from agent.codecompass.semantic_translation.config import (
    load_semantic_translation_config,
)
from agent.codecompass.semantic_translation.registry import (
    SemanticAdapterRegistry,
    SemanticGraphExecutionPort,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
    MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
    MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)
from worker.retrieval.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
    CodeCompassDomainSupplementSourceWriter,
)
from worker.retrieval.repository_codecompass_domain_admission import (
    _AcceptedSemanticDomain,
    _DomainAdmissionEvidence,
    _SemanticDomainEvidence,
    _TopLevelDomainPartitions,
)
from worker.retrieval.repository_codecompass_semantic_budget import (
    _DEFERRED_EDGE_DOMAIN_FIELD,
    _BoundedSemanticEdgeSpool,
    _BoundedSemanticGraphCollector,
    _canonical_json,
)

# Compatibility re-exports: names that were importable from this module
# before its collaborators were extracted.
from ananta_contracts.codecompass_graph_limits import (  # noqa: F401,I001
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (  # noqa: F401
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (  # noqa: F401
    SemanticDomainIdentity,
)
from worker.retrieval.repository_codecompass_domain_admission import (  # noqa: F401
    _DOMAIN_ADMISSION_STRATEGY,
)


class RepositoryGraphExecutionDeadlinePort(Protocol):
    def checkpoint(self) -> None: ...


def _checkpoint(
    execution_deadline: RepositoryGraphExecutionDeadlinePort | None,
) -> None:
    if execution_deadline is not None:
        execution_deadline.checkpoint()



class RepositoryCodeCompassBridge:
    """Project authorized file records into generic and semantic graph JSONL."""

    def __init__(
        self,
        semantic_graph: SemanticGraphExecutionPort | None = None,
        *,
        max_semantic_records_per_partition: int | None = None,
        max_semantic_edge_candidates: int = (MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES),
        max_semantic_edge_candidate_bytes: int = (MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES),
    ) -> None:
        self._semantic_graph = semantic_graph or SemanticAdapterRegistry()
        configured_default = load_semantic_translation_config().max_graph_records
        configured_limit = int(
            configured_default if max_semantic_records_per_partition is None else max_semantic_records_per_partition
        )
        if configured_limit <= 0:
            raise ValueError("semantic_graph_record_limit_invalid")
        self._configured_semantic_records_per_partition = configured_limit
        self._max_semantic_records_per_partition = min(
            configured_limit,
            MAX_CODECOMPASS_SEMANTIC_RECORDS_PER_PARTITION,
        )
        self._max_semantic_edge_candidates = self._contract_limit(
            max_semantic_edge_candidates,
            maximum=MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
            error="semantic_graph_edge_candidate_limit_invalid",
        )
        self._max_semantic_edge_candidate_bytes = self._contract_limit(
            max_semantic_edge_candidate_bytes,
            maximum=MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATE_BYTES,
            error="semantic_graph_edge_candidate_byte_limit_invalid",
        )

    def build_outputs(
        self,
        *,
        source_id: str,
        records: Sequence[Mapping[str, Any]],
        output_dir: Path,
        execution_deadline: RepositoryGraphExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        _checkpoint(execution_deadline)
        normalized = self._normalized_records(records)
        if not normalized:
            raise ValueError("repository_graph_source_records_empty")

        output_dir.mkdir(parents=True, exist_ok=True)
        root_id = self._node_id("repository", str(source_id))
        graph_nodes: dict[str, dict[str, Any]] = {
            root_id: {
                "id": root_id,
                "kind": "repository",
                "name": str(source_id),
            }
        }
        graph_edges: dict[str, dict[str, Any]] = {}

        # Materialize the source-grounded repository tree first. Semantic file
        # endpoints can then be bound during the one adapter pass below.
        for path, record in normalized:
            _checkpoint(execution_deadline)
            file_id = self._node_id("file", path)
            graph_nodes[file_id] = {
                "id": file_id,
                "kind": "source_file",
                "name": Path(path).name,
                "file": path,
                "path": path,
                "line_count": self._line_count(record.get("content")),
            }
            parent_id = self._materialize_directories(
                path=path,
                root_id=root_id,
                nodes=graph_nodes,
                edges=graph_edges,
            )
            self._add_graph_edge(
                graph_edges,
                source=parent_id,
                target=file_id,
                edge_type="contains_file",
            )

        semantic_result = self._materialize_semantic_partitions(
            normalized=normalized,
            output_dir=output_dir,
            graph_node_ids=set(graph_nodes),
            graph_edges=graph_edges,
            execution_deadline=execution_deadline,
        )

        _checkpoint(execution_deadline)
        self._write_jsonl(output_dir / "graph_nodes.jsonl", graph_nodes.values())
        self._write_jsonl(output_dir / "graph_edges.jsonl", graph_edges.values())
        _checkpoint(execution_deadline)
        return {
            "schema": "ananta.repository-codecompass-bridge.v1",
            "file_count": len(normalized),
            "graph_node_count": len(graph_nodes),
            "graph_edge_count": len(graph_edges),
            "semantic_node_count": semantic_result["semantic_node_count"],
            "semantic_edge_count": semantic_result["semantic_edge_count"],
            "semantic_file_count": semantic_result["semantic_file_count"],
            "diagnostic_count": semantic_result["diagnostic_count"],
            "semantic_budget": semantic_result["semantic_budget"],
            "partitioned_outputs": {
                "graph_nodes": ["graph_nodes.jsonl"],
                "graph_edges": ["graph_edges.jsonl"],
                "semantic_nodes": semantic_result["semantic_node_outputs"],
                "semantic_edges": semantic_result["semantic_edge_outputs"],
            },
        }

    def _materialize_semantic_partitions(
        self,
        *,
        normalized: Sequence[tuple[str, dict[str, Any]]],
        output_dir: Path,
        graph_node_ids: set[str],
        graph_edges: dict[str, dict[str, Any]],
        execution_deadline: RepositoryGraphExecutionDeadlinePort | None,
    ) -> dict[str, Any]:
        """Materialize one independently bounded semantic shard per domain.

        Adapter execution remains a single worker-side pass.  Accepted shard
        payloads are bounded both individually and in aggregate; deferred
        cross-domain edges use the existing bounded reservoir and are resolved
        only after all accepted node shards are known.
        """

        partitions = _TopLevelDomainPartitions(normalized)
        accepted: list[_AcceptedSemanticDomain] = []
        domain_evidence: list[_SemanticDomainEvidence] = []
        global_semantic_node_ids: set[str] = set()
        diagnostic_count = 0
        omitted_domain_count = 0
        empty_domain_count = 0
        omitted_truncated_nodes = 0
        omitted_truncated_edges = 0
        omitted_truncated_candidates = 0
        local_truncated_candidates = 0
        aggregate_output_bytes = 0

        supplement_source_path = output_dir / DOMAIN_SUPPLEMENT_SOURCE_FILENAME
        with CodeCompassDomainSupplementSourceWriter(
            supplement_source_path,
            execution_deadline=execution_deadline,
        ) as supplement_writer, _BoundedSemanticEdgeSpool(
            max_records=self._max_semantic_edge_candidates,
            max_bytes=self._max_semantic_edge_candidate_bytes,
        ) as deferred_edges:
            for identity, domain_records in partitions.groups():
                _checkpoint(execution_deadline)
                domain_key = identity.domain_key
                supplement_writer.add_domain(
                    identity,
                    source_file_count=len(domain_records),
                )
                collector = _BoundedSemanticGraphCollector(
                    max_records_per_partition=(self._max_semantic_records_per_partition),
                    max_bytes_per_partition=(MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION),
                )
                domain_graph_edges: dict[str, dict[str, Any]] = {}
                domain_declaration_bytes = 0
                domain_truncated_declarations = 0
                domain_semantic_file_count = 0
                domain_had_candidates = False
                with _BoundedSemanticEdgeSpool(
                    max_records=self._max_semantic_edge_candidates,
                    max_bytes=self._max_semantic_edge_candidate_bytes,
                ) as domain_deferred_edges:
                    for path, record in domain_records:
                        _checkpoint(execution_deadline)
                        content = record.get("content")
                        if not isinstance(content, str):
                            continue
                        emitted = self._semantic_graph.emit_graph_records(
                            path,
                            content,
                        )
                        _checkpoint(execution_deadline)
                        if not isinstance(emitted, Mapping):
                            raise RuntimeError(
                                "codecompass_domain_supplement_adapter_result_invalid"
                            )
                        raw_nodes = emitted.get("nodes") or []
                        raw_edges = emitted.get("edges") or []
                        raw_diagnostics = emitted.get("diagnostics") or []
                        if not isinstance(raw_nodes, (list, tuple)) or not isinstance(
                            raw_edges, (list, tuple)
                        ) or not isinstance(raw_diagnostics, (list, tuple)):
                            raise RuntimeError(
                                "codecompass_domain_supplement_adapter_collections_invalid"
                            )
                        diagnostic_count += len(raw_diagnostics)
                        supplement_writer.observe_diagnostics(
                            domain_key=domain_key,
                            diagnostics=raw_diagnostics,
                            emitted_record_count=len(raw_nodes) + len(raw_edges),
                        )
                        emitted_node_ids: list[str] = []
                        complete_emitted_node_ids: list[str] = []
                        for raw_node in raw_nodes:
                            if not isinstance(raw_node, Mapping):
                                raise RuntimeError(
                                    "codecompass_domain_supplement_node_invalid"
                                )
                            node = {
                                **self._stable_semantic_record(raw_node),
                                CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: domain_key,
                            }
                            node_id = str(node.get("id") or "").strip()
                            if not node_id:
                                raise RuntimeError(
                                    "codecompass_domain_supplement_node_invalid"
                                )
                            supplement_writer.add_node(
                                domain_key=domain_key,
                                record=node,
                            )
                            complete_emitted_node_ids.append(node_id)
                            domain_had_candidates = True
                            accepted_id = node_id if node_id in global_semantic_node_ids else collector.add_node(node)
                            if accepted_id:
                                emitted_node_ids.append(accepted_id)
                        file_id = self._node_id("file", path)
                        for node_id in sorted(set(complete_emitted_node_ids)):
                            supplement_writer.add_declaration_edge(
                                domain_key=domain_key,
                                record={
                                    "source": file_id,
                                    "target": node_id,
                                    "type": "declares",
                                    "directed": True,
                                    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: domain_key,
                                },
                            )
                        if emitted_node_ids:
                            domain_semantic_file_count += 1
                            for node_id in sorted(set(emitted_node_ids)):
                                (
                                    declaration_added,
                                    domain_declaration_bytes,
                                ) = self._add_bounded_graph_edge(
                                    domain_graph_edges,
                                    source=file_id,
                                    target=node_id,
                                    edge_type="declares",
                                    current_bytes=(domain_declaration_bytes),
                                    max_records=(self._max_semantic_records_per_partition),
                                    max_bytes=(MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION),
                                )
                                if not declaration_added:
                                    domain_truncated_declarations += 1
                                    collector.truncated_edge_count += 1
                        additional_node_ids = graph_node_ids | global_semantic_node_ids
                        for raw_edge in raw_edges:
                            if not isinstance(raw_edge, Mapping):
                                raise RuntimeError(
                                    "codecompass_domain_supplement_edge_invalid"
                                )
                            domain_had_candidates = True
                            edge = {
                                **self._normalize_current_file_endpoint(
                                    self._stable_semantic_record(raw_edge),
                                    path=path,
                                    file_id=file_id,
                                ),
                                CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD: domain_key,
                            }
                            supplement_writer.add_semantic_edge(
                                domain_key=domain_key,
                                record=edge,
                            )
                            if collector.can_resolve_edge(
                                edge,
                                additional_node_ids=additional_node_ids,
                            ):
                                collector.add_edge(
                                    edge,
                                    additional_node_ids=additional_node_ids,
                                )
                            else:
                                domain_deferred_edges.append(edge)

                    domain_candidate_truncation = domain_deferred_edges.truncated_edge_count
                    collector.truncated_edge_count += domain_candidate_truncation
                    domain_bytes = collector.node_bytes + collector.edge_bytes + domain_declaration_bytes
                    domain_has_material = bool(
                        collector.nodes or collector.edges or domain_graph_edges or domain_deferred_edges.record_count
                    )
                    domain_admissible = (
                        domain_has_material
                        and len(accepted) < MAX_CODECOMPASS_SEMANTIC_PARTITIONS
                        and aggregate_output_bytes + domain_bytes <= MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES
                    )
                    if not domain_had_candidates:
                        empty_domain_count += 1
                        domain_evidence.append(
                            _SemanticDomainEvidence(
                                identity=identity,
                                status="no_semantic_records",
                                source_file_count=len(domain_records),
                                semantic_file_count=0,
                                semantic_node_count=0,
                                semantic_edge_count=0,
                                semantic_node_bytes=0,
                                semantic_edge_bytes=0,
                                graph_declaration_count=0,
                                graph_declaration_bytes=0,
                                truncated_graph_declaration_count=0,
                                truncated_node_count=0,
                                truncated_edge_count=0,
                                unresolved_edge_count=0,
                            )
                        )
                        continue
                    if not domain_admissible:
                        if not domain_has_material:
                            omission_status = "per_partition_limit"
                        elif len(accepted) >= MAX_CODECOMPASS_SEMANTIC_PARTITIONS:
                            omission_status = "partition_limit"
                        else:
                            omission_status = "aggregate_byte_limit"
                        domain_omitted_nodes = len(collector.nodes) + collector.truncated_node_count
                        domain_omitted_edges = (
                            len(collector.edges)
                            + collector.truncated_edge_count
                            + domain_deferred_edges.record_count
                            + len(domain_graph_edges)
                        )
                        omitted_domain_count += 1
                        omitted_truncated_nodes += domain_omitted_nodes
                        omitted_truncated_edges += domain_omitted_edges
                        omitted_truncated_candidates += domain_candidate_truncation + domain_deferred_edges.record_count
                        domain_evidence.append(
                            _SemanticDomainEvidence(
                                identity=identity,
                                status=omission_status,
                                source_file_count=len(domain_records),
                                semantic_file_count=0,
                                semantic_node_count=0,
                                semantic_edge_count=0,
                                semantic_node_bytes=0,
                                semantic_edge_bytes=0,
                                graph_declaration_count=0,
                                graph_declaration_bytes=0,
                                truncated_graph_declaration_count=(
                                    len(domain_graph_edges) + domain_truncated_declarations
                                ),
                                truncated_node_count=domain_omitted_nodes,
                                truncated_edge_count=domain_omitted_edges,
                                unresolved_edge_count=0,
                            )
                        )
                        continue

                    aggregate_output_bytes += domain_bytes
                    global_semantic_node_ids.update(collector.nodes)
                    graph_edges.update(domain_graph_edges)
                    accepted.append(
                        _AcceptedSemanticDomain(
                            identity=identity,
                            collector=collector,
                            source_file_count=len(domain_records),
                            semantic_file_count=domain_semantic_file_count,
                            graph_declaration_count=len(domain_graph_edges),
                            graph_declaration_bytes=(domain_declaration_bytes),
                            truncated_graph_declaration_count=(domain_truncated_declarations),
                        )
                    )
                    local_truncated_candidates += domain_candidate_truncation
                    for deferred in domain_deferred_edges.records():
                        deferred_edges.append(
                            {
                                **deferred,
                                _DEFERRED_EDGE_DOMAIN_FIELD: identity.domain_key,
                            }
                        )

            accepted_by_domain = {
                partition.identity.domain_key: partition for partition in accepted
            }
            all_available_node_ids = graph_node_ids | global_semantic_node_ids
            for raw_deferred in deferred_edges.records():
                _checkpoint(execution_deadline)
                deferred = dict(raw_deferred)
                domain = str(deferred.pop(_DEFERRED_EDGE_DOMAIN_FIELD, ""))
                partition = accepted_by_domain.get(domain)
                if partition is None:
                    continue
                record_bytes = _BoundedSemanticGraphCollector._record_bytes(deferred)
                if aggregate_output_bytes + record_bytes > MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES:
                    partition.collector.truncated_edge_count += 1
                    continue
                before_bytes = partition.collector.edge_bytes
                partition.collector.add_edge(
                    deferred,
                    additional_node_ids=all_available_node_ids,
                )
                aggregate_output_bytes += partition.collector.edge_bytes - before_bytes

            global_lost_candidates_by_domain = deferred_edges.lost_by_domain
            global_unattributed_candidate_truncation = deferred_edges.unattributed_truncated_edge_count
            global_candidate_truncation = (
                sum(global_lost_candidates_by_domain.values()) + global_unattributed_candidate_truncation
            )
            for domain, lost_count in global_lost_candidates_by_domain.items():
                partition = accepted_by_domain.get(domain)
                if partition is None:
                    global_unattributed_candidate_truncation += lost_count
                    continue
                partition.collector.truncated_edge_count += lost_count
            candidate_record_count = deferred_edges.record_count
            candidate_byte_count = deferred_edges.byte_count
            supplement_writer.finalize()

        (
            semantic_node_outputs,
            semantic_edge_outputs,
            physical_partition_count,
        ) = self._write_semantic_partition_files(
            output_dir=output_dir,
            accepted=accepted,
        )
        semantic_node_count = sum(len(partition.collector.nodes) for partition in accepted)
        semantic_edge_count = sum(len(partition.collector.edges) for partition in accepted)
        semantic_node_bytes = sum(partition.collector.node_bytes for partition in accepted)
        semantic_edge_bytes = sum(partition.collector.edge_bytes for partition in accepted)
        truncated_node_count = omitted_truncated_nodes + sum(
            partition.collector.truncated_node_count for partition in accepted
        )
        truncated_candidate_edge_count = (
            local_truncated_candidates + global_candidate_truncation + omitted_truncated_candidates
        )
        truncated_edge_count = (
            omitted_truncated_edges
            + global_unattributed_candidate_truncation
            + sum(partition.collector.truncated_edge_count for partition in accepted)
        )
        unresolved_edge_count = sum(partition.collector.unresolved_edge_count for partition in accepted)
        domain_evidence.extend(
            _SemanticDomainEvidence(
                identity=partition.identity,
                status="materialized",
                source_file_count=partition.source_file_count,
                semantic_file_count=partition.semantic_file_count,
                semantic_node_count=len(partition.collector.nodes),
                semantic_edge_count=len(partition.collector.edges),
                semantic_node_bytes=partition.collector.node_bytes,
                semantic_edge_bytes=partition.collector.edge_bytes,
                graph_declaration_count=(partition.graph_declaration_count),
                graph_declaration_bytes=(partition.graph_declaration_bytes),
                truncated_graph_declaration_count=(partition.truncated_graph_declaration_count),
                truncated_node_count=(partition.collector.truncated_node_count),
                truncated_edge_count=(partition.collector.truncated_edge_count),
                unresolved_edge_count=(partition.collector.unresolved_edge_count),
            )
            for partition in accepted
        )
        admission = _DomainAdmissionEvidence(
            domain_count=partitions.domain_count,
            materialized_domain_count=len(accepted),
            omitted_domain_count=omitted_domain_count,
            empty_domain_count=empty_domain_count,
            partition_count=physical_partition_count,
            domains=domain_evidence,
        )
        semantic_budget = {
            "configured_max_records_per_partition": (self._configured_semantic_records_per_partition),
            "max_records_per_partition": (self._max_semantic_records_per_partition),
            "max_bytes_per_partition": (MAX_CODECOMPASS_SEMANTIC_BYTES_PER_PARTITION),
            "configuration_clamped": (
                self._configured_semantic_records_per_partition != self._max_semantic_records_per_partition
            ),
            "truncated": bool(truncated_node_count or truncated_edge_count),
            "truncated_node_count": truncated_node_count,
            "truncated_edge_count": truncated_edge_count,
            "unresolved_edge_count": unresolved_edge_count,
            "semantic_node_bytes": semantic_node_bytes,
            "semantic_edge_bytes": semantic_edge_bytes,
            "candidate_edge_record_limit": (self._max_semantic_edge_candidates),
            "candidate_edge_byte_limit": (self._max_semantic_edge_candidate_bytes),
            "candidate_edge_count": candidate_record_count,
            "candidate_edge_bytes": candidate_byte_count,
            "truncated_candidate_edge_count": (truncated_candidate_edge_count),
            "domain_admission": admission.to_wire(),
        }
        return {
            "semantic_node_count": semantic_node_count,
            "semantic_edge_count": semantic_edge_count,
            "semantic_file_count": sum(partition.semantic_file_count for partition in accepted),
            "diagnostic_count": diagnostic_count,
            "semantic_budget": semantic_budget,
            "semantic_node_outputs": semantic_node_outputs,
            "semantic_edge_outputs": semantic_edge_outputs,
            "domain_supplement_source": DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
        }

    @classmethod
    def _write_semantic_partition_files(
        cls,
        *,
        output_dir: Path,
        accepted: Sequence[_AcceptedSemanticDomain],
    ) -> tuple[list[str], list[str], int]:
        materialized = list(accepted)
        if len(materialized) <= 1:
            collector = (
                materialized[0].collector
                if materialized
                else _BoundedSemanticGraphCollector(
                    max_records_per_partition=1,
                    max_bytes_per_partition=1,
                )
            )
            cls._write_jsonl(
                output_dir / "semantic_nodes.jsonl",
                collector.nodes.values(),
            )
            cls._write_jsonl(
                output_dir / "semantic_edges.jsonl",
                collector.edges.values(),
            )
            return (
                ["semantic_nodes.jsonl"],
                ["semantic_edges.jsonl"],
                1,
            )

        node_outputs: list[str] = []
        edge_outputs: list[str] = []
        for partition in materialized:
            digest = partition.identity.domain_key.removeprefix("sha256:")
            node_filename = f"semantic_nodes.domain-{digest}.jsonl"
            edge_filename = f"semantic_edges.domain-{digest}.jsonl"
            cls._write_jsonl(
                output_dir / node_filename,
                partition.collector.nodes.values(),
            )
            cls._write_jsonl(
                output_dir / edge_filename,
                partition.collector.edges.values(),
            )
            node_outputs.append(node_filename)
            edge_outputs.append(edge_filename)
        return node_outputs, edge_outputs, len(materialized)

    @staticmethod
    def _contract_limit(value: int, *, maximum: int, error: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
            raise ValueError(error)
        return value

    @classmethod
    def _normalized_records(
        cls,
        records: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        by_path: dict[str, dict[str, Any]] = {}
        for raw in records:
            record = dict(raw)
            path = cls._relative_path(record)
            if not path:
                continue
            current = by_path.get(path)
            if current is None or _canonical_json(record) < _canonical_json(current):
                by_path[path] = record
        return [(path, by_path[path]) for path in sorted(by_path)]

    @staticmethod
    def _relative_path(record: Mapping[str, Any]) -> str:
        metadata = record.get("metadata")
        values = metadata if isinstance(metadata, Mapping) else {}
        raw = str(
            values.get("relative_path") or record.get("file") or record.get("path") or record.get("id") or ""
        ).replace("\\", "/")
        parts = raw.split("/")
        if not raw or raw.startswith("/") or "\x00" in raw or any(part in {"", ".", ".."} for part in parts):
            return ""
        return "/".join(parts)

    @classmethod
    def _materialize_directories(
        cls,
        *,
        path: str,
        root_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: dict[str, dict[str, Any]],
    ) -> str:
        parts = path.split("/")[:-1]
        parent_id = root_id
        for depth in range(1, len(parts) + 1):
            directory = "/".join(parts[:depth])
            directory_id = cls._node_id("directory", directory)
            nodes.setdefault(
                directory_id,
                {
                    "id": directory_id,
                    "kind": "directory",
                    "name": parts[depth - 1],
                    "path": directory,
                },
            )
            cls._add_graph_edge(
                edges,
                source=parent_id,
                target=directory_id,
                edge_type="contains_directory",
            )
            parent_id = directory_id
        return parent_id

    @staticmethod
    def _stable_semantic_record(raw: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(raw)
        attributes = record.get("attributes")
        if isinstance(attributes, Mapping):
            stable_attributes = dict(attributes)
            # Semantic adapters also emit every method as a function_signature
            # node connected through a declares edge. Keeping the full nested
            # method snapshots here duplicates large parameter/type payloads
            # without adding graph information.
            stable_attributes.pop("methods", None)
            record["attributes"] = stable_attributes
        provenance = record.get("provenance")
        if isinstance(provenance, Mapping):
            stable_provenance = dict(provenance)
            stable_provenance.pop("created_at", None)
            record["provenance"] = stable_provenance
        return record

    @staticmethod
    def _normalize_current_file_endpoint(
        edge: dict[str, Any],
        *,
        path: str,
        file_id: str,
    ) -> dict[str, Any]:
        """Bind adapter-declared semantic file endpoints to the source file node."""

        normalized_path = path.replace("\\", "/").removeprefix("./")
        result = dict(edge)
        attributes = dict(result.get("attributes") or {})
        for endpoint in ("source", "source_id", "target", "target_id"):
            value = str(result.get(endpoint) or "").strip()
            prefix, separator, endpoint_path = value.partition(":file:")
            normalized_endpoint_path = endpoint_path.replace("\\", "/").removeprefix("./")
            if separator and prefix.startswith("semantic:") and normalized_endpoint_path == normalized_path:
                result[endpoint] = file_id
                attributes[f"{endpoint}_endpoint_original"] = value
                attributes[f"{endpoint}_endpoint_binding"] = "repository_source_file"
        if attributes:
            result["attributes"] = attributes
        return result

    @staticmethod
    def _semantic_edge_is_closed(
        edge: Mapping[str, Any],
        *,
        semantic_node_ids: Mapping[str, object],
        graph_node_ids: set[str],
    ) -> bool:
        source = str(edge.get("source") or edge.get("source_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_id") or "").strip()
        return bool(
            source
            and target
            and (source in semantic_node_ids or source in graph_node_ids)
            and (target in semantic_node_ids or target in graph_node_ids)
        )

    @staticmethod
    def _node_id(kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
        return f"source:{kind}:{digest}"

    @staticmethod
    def _line_count(content: object) -> int:
        if not isinstance(content, str) or not content:
            return 0
        return content.count("\n") + 1

    @staticmethod
    def _add_graph_edge(
        edges: dict[str, dict[str, Any]],
        *,
        source: str,
        target: str,
        edge_type: str,
    ) -> None:
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "directed": True,
        }
        edges.setdefault(_canonical_json(edge), edge)

    @staticmethod
    def _add_bounded_graph_edge(
        edges: dict[str, dict[str, Any]],
        *,
        source: str,
        target: str,
        edge_type: str,
        current_bytes: int,
        max_records: int,
        max_bytes: int,
    ) -> tuple[bool, int]:
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "directed": True,
        }
        identity = _canonical_json(edge)
        if identity in edges:
            return True, current_bytes
        record_bytes = len((identity + "\n").encode("utf-8"))
        if len(edges) >= max_records or current_bytes + record_bytes > max_bytes:
            return False, current_bytes
        edges[identity] = edge
        return True, current_bytes + record_bytes

    @staticmethod
    def _write_jsonl(path: Path, records: object) -> None:
        rows = sorted(_canonical_json(dict(item)) for item in records)
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


__all__ = ["RepositoryCodeCompassBridge"]
