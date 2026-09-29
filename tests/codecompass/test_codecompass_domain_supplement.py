from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (
    DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
    CodeCompassDomainSupplementSourceWriter,
    SemanticDomainIdentity,
    WorkerCodeCompassDomainSupplementMaterializer,
)
from worker.retrieval.codecompass_graph_artifact_materializer import (
    WorkerCodeCompassGraphArtifactMaterializer,
)
from worker.retrieval.knowledge_index_job_handler import (
    WorkerKnowledgeIndexArtifactPublisher,
)
from worker.retrieval.repository_codecompass_bridge import (
    RepositoryCodeCompassBridge,
)

_GRAPH_REVISION = "sha256:" + "a" * 64
_SOURCE_REVISION_ID = "srev_" + "b" * 64
_SOURCE_REVISION_DIGEST = "c" * 64
_SOURCE_ID = f"bound-source:{_SOURCE_REVISION_ID}"


def _identity(label: str) -> SemanticDomainIdentity:
    return SemanticDomainIdentity(
        domain_key=codecompass_semantic_domain_key(label),
        domain_kind="top_level_path",
        domain_label=label,
    )


def _root_identity() -> SemanticDomainIdentity:
    return SemanticDomainIdentity(
        domain_key=codecompass_semantic_repository_root_domain_key(),
        domain_kind="repository_root",
        domain_label="",
    )


def _write_source(path: Path, *, reverse: bool) -> Path:
    domains = [_root_identity(), _identity("src"), _identity("__repository_root__")]
    if reverse:
        domains.reverse()
    with CodeCompassDomainSupplementSourceWriter(path) as writer:
        for identity in domains:
            writer.add_domain(identity, source_file_count=1)
        records = [
            (
                _identity("src"),
                {
                    "id": "semantic:src:A",
                    "kind": "semantic_node",
                    "file": "src/a.py",
                },
            ),
            (
                _identity("__repository_root__"),
                {
                    "id": "semantic:sentinel:B",
                    "kind": "semantic_node",
                    "file": "__repository_root__/b.py",
                },
            ),
        ]
        if reverse:
            records.reverse()
        for identity, record in records:
            writer.add_node(domain_key=identity.domain_key, record=record)
            writer.add_declaration_edge(
                domain_key=identity.domain_key,
                record={
                    "source": "source:file:" + identity.domain_key[-8:],
                    "target": record["id"],
                    "type": "declares",
                    "directed": True,
                },
            )
        writer.add_semantic_edge(
            domain_key=_identity("src").domain_key,
            record={
                "source": "semantic:src:A",
                "target": "semantic:external",
                "type": "uses_type",
                "directed": True,
            },
        )
        return writer.finalize()


def _materialize(source: Path, destination: Path) -> dict[str, Any]:
    return WorkerCodeCompassDomainSupplementMaterializer().materialize(
        source_path=source,
        output_path=destination,
        graph_revision=_GRAPH_REVISION,
        source_scope="repo_path",
        knowledge_index_id="kidx_test",
        source_id=_SOURCE_ID,
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
    )


def test_supplement_is_physically_deterministic_and_keeps_empty_domains(
    tmp_path: Path,
) -> None:
    first_source = _write_source(tmp_path / "first-source.sqlite3", reverse=False)
    second_source = _write_source(tmp_path / "second-source.sqlite3", reverse=True)

    first = _materialize(first_source, tmp_path / "first.sqlite3")
    second = _materialize(second_source, tmp_path / "second.sqlite3")

    assert (tmp_path / "first.sqlite3").read_bytes() == (
        tmp_path / "second.sqlite3"
    ).read_bytes()
    assert first["graph_content_hash"] == second["graph_content_hash"]
    assert first["domain_count"] == 3
    assert first["semantic_node_count"] == 2
    assert first["semantic_edge_count"] == 1
    assert first["declaration_edge_count"] == 2
    assert codecompass_semantic_repository_root_domain_key() != (
        codecompass_semantic_domain_key("__repository_root__")
    )

    with sqlite3.connect(tmp_path / "first.sqlite3") as connection:
        root = connection.execute(
            "SELECT domain_kind, domain_label, semantic_node_count, complete "
            "FROM domains WHERE domain_key = ?",
            (codecompass_semantic_repository_root_domain_key(),),
        ).fetchone()
    assert root == ("repository_root", "", 0, 1)


def test_supplement_fails_closed_for_incomplete_adapter_output(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "source.sqlite3"
    with pytest.raises(RuntimeError, match="codecompass_domain_supplement_incomplete"):
        with CodeCompassDomainSupplementSourceWriter(destination) as writer:
            identity = _identity("src")
            writer.add_domain(identity, source_file_count=1)
            writer.observe_diagnostics(
                domain_key=identity.domain_key,
                diagnostics=[{"code": "parser_limit_exceeded"}],
                emitted_record_count=0,
            )
            writer.finalize()

    assert not destination.exists()
    assert not list(tmp_path.glob(".source.sqlite3.*.tmp"))


def test_supplement_fails_closed_at_the_configured_sqlite_budget(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)
    destination = tmp_path / "too-small.sqlite3"

    with pytest.raises(RuntimeError, match="codecompass_domain_supplement_too_large"):
        WorkerCodeCompassDomainSupplementMaterializer(
            maximum_bytes=4096
        ).materialize(
            source_path=source,
            output_path=destination,
            graph_revision=_GRAPH_REVISION,
            source_scope="repo_path",
            knowledge_index_id="kidx_test",
            source_id=_SOURCE_ID,
            source_revision_id=_SOURCE_REVISION_ID,
            source_revision_digest=_SOURCE_REVISION_DIGEST,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".too-small.sqlite3.*.tmp"))


def test_source_writer_fails_closed_at_its_raw_disk_budget(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "bounded-source.sqlite3"
    identity = _identity("src")

    with pytest.raises(
        RuntimeError,
        match="codecompass_domain_supplement_source_too_large",
    ):
        with CodeCompassDomainSupplementSourceWriter(
            destination,
            maximum_bytes=64 * 1024,
        ) as writer:
            writer.add_domain(identity, source_file_count=1)
            for index in range(1_000):
                writer.add_node(
                    domain_key=identity.domain_key,
                    record={
                        "id": f"semantic:src:{index}",
                        "kind": "semantic_node",
                        "payload": "x" * 4_000,
                    },
                )
            writer.finalize()

    assert not destination.exists()
    assert not list(tmp_path.glob(".bounded-source.sqlite3.*.tmp"))


def test_source_writer_uses_scalable_configured_disk_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = 1024 * 1024 * 1024
    monkeypatch.setenv(
        "ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES",
        str(configured),
    )

    with CodeCompassDomainSupplementSourceWriter(
        tmp_path / "configured-source.sqlite3"
    ) as writer:
        assert writer._maximum_bytes == configured

    monkeypatch.delenv(
        "ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES"
    )
    with CodeCompassDomainSupplementSourceWriter(
        tmp_path / "default-source.sqlite3"
    ) as writer:
        assert (
            writer._maximum_bytes
            == DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES
        )


@pytest.mark.parametrize(
    "configured",
    [
        "invalid",
        str(64 * 1024 - 1),
        str(MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES + 1),
    ],
)
def test_source_writer_rejects_invalid_configured_disk_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv(
        "ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES",
        configured,
    )

    with pytest.raises(
        ValueError,
        match="codecompass_domain_supplement_source_limit_invalid",
    ):
        CodeCompassDomainSupplementSourceWriter(
            tmp_path / "invalid-source.sqlite3"
        )


def test_source_reader_rejects_unexpected_sqlite_schema(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT)")

    with pytest.raises(
        ValueError,
        match="codecompass_domain_supplement_source_schema_invalid",
    ):
        WorkerCodeCompassDomainSupplementMaterializer().inspect_source(source)


class _ExpiringDeadline:
    def __init__(self, *, fail_after: int) -> None:
        self.calls = 0
        self._fail_after = fail_after

    def checkpoint(self) -> None:
        self.calls += 1
        if self.calls >= self._fail_after:
            raise RuntimeError("test_supplement_deadline_expired")


def test_supplement_materialization_honors_deadline_and_cleans_temporary_file(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)
    destination = tmp_path / "published.sqlite3"
    deadline = _ExpiringDeadline(fail_after=8)

    with pytest.raises(RuntimeError, match="test_supplement_deadline_expired"):
        WorkerCodeCompassDomainSupplementMaterializer().materialize(
            source_path=source,
            output_path=destination,
            graph_revision=_GRAPH_REVISION,
            source_scope="repo_path",
            knowledge_index_id="kidx_test",
            source_id=_SOURCE_ID,
            source_revision_id=_SOURCE_REVISION_ID,
            source_revision_digest=_SOURCE_REVISION_DIGEST,
            execution_deadline=deadline,
        )

    assert deadline.calls == 8
    assert not destination.exists()
    assert not list(tmp_path.glob(".published.sqlite3.*.tmp"))


def test_supplement_rejects_local_path_as_published_source_identity(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)

    with pytest.raises(
        ValueError,
        match="codecompass_domain_supplement_source_revision_invalid",
    ):
        WorkerCodeCompassDomainSupplementMaterializer().materialize(
            source_path=source,
            output_path=tmp_path / "published.sqlite3",
            graph_revision=_GRAPH_REVISION,
            source_scope="repo_path",
            knowledge_index_id="kidx_test",
            source_id="/srv/private/repository",
            source_revision_id=_SOURCE_REVISION_ID,
            source_revision_digest=_SOURCE_REVISION_DIGEST,
        )


def test_publisher_rejects_supplement_without_revision_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / DOMAIN_SUPPLEMENT_FILENAME).write_bytes(b"not-published")

    with pytest.raises(
        RuntimeError,
        match="knowledge_index_revision_binding_missing",
    ):
        WorkerKnowledgeIndexArtifactPublisher().publish(
            job_id="knowledge-index-" + "a" * 32,
            knowledge_index={"id": "kidx_test"},
            run={"id": "run_test", "output_dir": str(tmp_path)},
        )


class _ManySemanticRecords:
    def __init__(self, count: int = 6) -> None:
        self.count = count

    def emit_graph_records(self, path: str, content: str) -> dict[str, Any]:
        del content
        nodes = [
            {
                "id": f"semantic:{path}:{index}",
                "kind": "semantic_node",
                "file": path,
            }
            for index in range(self.count)
        ]
        return {
            "nodes": nodes,
            "edges": [
                {
                    "source": nodes[index]["id"],
                    "target": nodes[index + 1]["id"],
                    "type": "uses_type",
                }
                for index in range(len(nodes) - 1)
            ],
            "diagnostics": [],
        }


def test_bridge_keeps_complete_domain_source_when_overview_is_truncated(
    tmp_path: Path,
) -> None:
    result = RepositoryCodeCompassBridge(
        _ManySemanticRecords(),
        max_semantic_records_per_partition=1,
    ).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "pass",
                "metadata": {"relative_path": "src/module.py"},
            }
        ],
        output_dir=tmp_path,
    )

    overview_nodes = (tmp_path / "semantic_nodes.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    content = WorkerCodeCompassDomainSupplementMaterializer().inspect_source(
        tmp_path / DOMAIN_SUPPLEMENT_SOURCE_FILENAME
    )

    assert len(overview_nodes) == 1
    assert result["semantic_budget"]["truncated"] is True
    assert content.semantic_node_count == 6
    assert content.semantic_edge_count == 5
    assert content.declaration_edge_count == 6


def test_supplement_rejects_payload_tampering(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)
    destination = tmp_path / "published.sqlite3"
    _materialize(source, destination)
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "UPDATE domain_payloads SET raw_sha256 = ? WHERE "
            "(domain_key, payload_kind, chunk_ordinal) = "
            "(SELECT domain_key, payload_kind, chunk_ordinal "
            "FROM domain_payloads ORDER BY domain_key, payload_kind, chunk_ordinal LIMIT 1)",
            ("0" * 64,),
        )

    with pytest.raises(ValueError, match="chunk_hash_mismatch"):
        WorkerCodeCompassDomainSupplementMaterializer.inspect_published(
            destination
        )


def test_domain_marker_is_bound_to_its_opaque_domain_key(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.sqlite3", reverse=False)
    with sqlite3.connect(source) as connection:
        marker = connection.execute(
            "SELECT record_json FROM semantic_nodes WHERE node_id = ?",
            ("semantic:src:A",),
        ).fetchone()[0]

    assert (
        f'"{CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD}":'
        f'"{codecompass_semantic_domain_key("src")}"'
    ) in marker


def _materialize_bound_repository_graph(
    output_dir: Path,
    *,
    semantic_count: int,
) -> dict[str, Any]:
    bridge_result = RepositoryCodeCompassBridge(
        _ManySemanticRecords(semantic_count),
        max_semantic_records_per_partition=1,
    ).build_outputs(
        source_id=_SOURCE_ID,
        records=[
            {
                "content": "pass",
                "metadata": {"relative_path": "src/module.py"},
            }
        ],
        output_dir=output_dir,
    )
    (output_dir / "index.jsonl").write_text(
        '{"file":"src/module.py","content":"pass"}\n',
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_scope": "repo_path",
                "source_id": _SOURCE_ID,
                "partitioned_outputs": bridge_result["partitioned_outputs"],
                "semantic_budget": bridge_result["semantic_budget"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    binding = {
        "source_scope": "repo_path",
        "source_id": _SOURCE_ID,
        "source_revision_id": _SOURCE_REVISION_ID,
        "source_revision_digest": _SOURCE_REVISION_DIGEST,
    }
    knowledge_index = {
        "id": "kidx_test",
        "source_scope": "repo_path",
        "index_metadata": dict(binding),
    }
    return WorkerCodeCompassGraphArtifactMaterializer().materialize(
        knowledge_index=knowledge_index,
        run={"id": "run_test", "output_dir": str(output_dir)},
        revision_binding=binding,
    )


def test_graph_revision_covers_complete_supplement_not_only_bounded_overview(
    tmp_path: Path,
) -> None:
    first = _materialize_bound_repository_graph(
        tmp_path / "first",
        semantic_count=6,
    )
    second = _materialize_bound_repository_graph(
        tmp_path / "second",
        semantic_count=7,
    )

    assert first["graph_revision"] != second["graph_revision"]
    assert first["domain_supplement"]["semantic_node_count"] == 6
    assert second["domain_supplement"]["semantic_node_count"] == 7
    for root, expected_count, expected_revision in (
        (tmp_path / "first", 6, first["graph_revision"]),
        (tmp_path / "second", 7, second["graph_revision"]),
    ):
        published = WorkerCodeCompassDomainSupplementMaterializer.inspect_published(
            root / DOMAIN_SUPPLEMENT_FILENAME
        )
        assert published["semantic_node_count"] == expected_count
        assert published["graph_revision"] == expected_revision
