"""Value types, errors and shared limits for CodeCompass domain supplements."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_PAYLOAD_KINDS,
    codecompass_domain_supplement_canonical_json_bytes,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
)

DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_DOMAIN_SUPPLEMENT_COMPRESSED_CHUNK_BYTES = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES + 128 * 1024
MAX_DOMAIN_SUPPLEMENT_IDENTIFIER_CHARACTERS = 4_096
DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET = frozenset(DOMAIN_SUPPLEMENT_PAYLOAD_KINDS)


def domain_supplement_canonical_json(value: object) -> str:
    return codecompass_domain_supplement_canonical_json_bytes(value).decode("utf-8")


def invoke_domain_supplement_checkpoint(callback: Callable[[], object] | None) -> None:
    if callback is not None:
        callback()


class CodeCompassDomainSupplementError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CodeCompassDomainSupplementBinding:
    knowledge_index_id: str
    source_revision_id: str
    source_revision_digest: str
    graph_revision: str
    artifact_sha256: str
    logical_content_hash: str
    source_scope: str = "repo_path"
    source_id: str | None = None


@dataclass(frozen=True)
class CodeCompassDomainSupplementSummary:
    domain_key: str
    domain_kind: str
    domain_label: str
    source_file_count: int
    semantic_node_count: int
    semantic_edge_count: int
    declaration_edge_count: int
    semantic_node_bytes: int
    semantic_edge_bytes: int
    declaration_edge_bytes: int
    complete: bool


@dataclass(frozen=True)
class CodeCompassDomainSupplementCatalog:
    graph_revision: str
    logical_content_hash: str
    domains: tuple[CodeCompassDomainSupplementSummary, ...]


@dataclass(frozen=True)
class CodeCompassDomainSupplementRecords:
    graph_revision: str
    logical_content_hash: str
    domain_keys: tuple[str, ...]
    nodes: tuple[Mapping[str, object], ...]
    semantic_edges: tuple[Mapping[str, object], ...]
    declaration_edges: tuple[Mapping[str, object], ...]
    semantic_node_count: int
    semantic_edge_count: int
    declaration_edge_count: int


@dataclass(frozen=True)
class CachedDomainSupplementRecords:
    records: tuple[
        tuple[Mapping[str, object], ...],
        tuple[Mapping[str, object], ...],
        tuple[Mapping[str, object], ...],
    ]
    raw_size: int


class CodeCompassDomainSupplementPort(Protocol):
    def validate_artifact(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog: ...

    def catalog(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog: ...

    def load_domains(
        self,
        *,
        path: Path,
        domain_keys: Sequence[str],
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementRecords: ...
