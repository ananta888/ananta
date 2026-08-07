from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import worker.retrieval.codecompass_domain_supplement as supplement_module
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (
    CodeCompassDomainSupplementSourceWriter,
    SemanticDomainIdentity,
    WorkerCodeCompassDomainSupplementMaterializer,
)

_GRAPH_REVISION = "sha256:" + "1" * 64
_SOURCE_REVISION_ID = "srev_" + "2" * 64
_SOURCE_REVISION_DIGEST = "3" * 64


def _source(path: Path) -> Path:
    identity = SemanticDomainIdentity(
        domain_key=codecompass_semantic_domain_key("src"),
        domain_kind="top_level_path",
        domain_label="src",
    )
    with CodeCompassDomainSupplementSourceWriter(path) as writer:
        writer.add_domain(identity, source_file_count=1)
        writer.add_node(
            domain_key=identity.domain_key,
            record={
                "id": "semantic:src:main",
                "kind": "semantic_node",
                "file": "src/main.py",
            },
        )
        writer.add_semantic_edge(
            domain_key=identity.domain_key,
            record={
                "source": "semantic:src:main",
                "target": "semantic:external",
                "type": "uses_type",
                "directed": True,
            },
        )
        writer.add_declaration_edge(
            domain_key=identity.domain_key,
            record={
                "source": "source:src/main.py",
                "target": "semantic:src:main",
                "type": "declares",
                "directed": True,
            },
        )
        return writer.finalize()


def _raw_payload_bytes(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return sum(
            int(
                connection.execute(
                    f"SELECT COALESCE(SUM(LENGTH(CAST(record_json AS BLOB)) + 1), 0) "
                    f"FROM {table}"
                ).fetchone()[0]
            )
            for table in (
                "semantic_nodes",
                "semantic_edges",
                "declaration_edges",
            )
        )


def _materialize(source: Path, destination: Path) -> dict[str, object]:
    return WorkerCodeCompassDomainSupplementMaterializer().materialize(
        source_path=source,
        output_path=destination,
        graph_revision=_GRAPH_REVISION,
        source_scope="repository",
        knowledge_index_id="index-test",
        source_id=f"bound-source:{_SOURCE_REVISION_ID}",
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
    )


def test_shared_raw_payload_budget_is_the_hub_compatible_384_mib() -> None:
    assert MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES == 384 * 1024 * 1024


def test_worker_accepts_exact_raw_budget_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source.sqlite3")
    raw_payload_bytes = _raw_payload_bytes(source)
    monkeypatch.setattr(
        supplement_module,
        "MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES",
        raw_payload_bytes,
    )

    result = _materialize(source, tmp_path / "accepted.sqlite3")

    assert result["size_bytes"] > 0
    assert (tmp_path / "accepted.sqlite3").is_file()


def test_worker_rejects_one_byte_over_raw_budget_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source.sqlite3")
    raw_payload_bytes = _raw_payload_bytes(source)
    destination = tmp_path / "rejected.sqlite3"
    monkeypatch.setattr(
        supplement_module,
        "MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES",
        raw_payload_bytes - 1,
    )

    with pytest.raises(
        ValueError,
        match="codecompass_domain_supplement_raw_budget_exceeded",
    ):
        _materialize(source, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite3.*.tmp"))
