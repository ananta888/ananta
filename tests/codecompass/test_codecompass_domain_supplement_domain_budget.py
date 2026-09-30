from __future__ import annotations

from pathlib import Path

import pytest

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (
    CodeCompassDomainSupplementSourceWriter,
    DomainSupplementLogicalContentReader,
    SemanticDomainIdentity,
    WorkerCodeCompassDomainSupplementMaterializer,
)

_GRAPH_REVISION = "sha256:" + "4" * 64
_SOURCE_REVISION_ID = "srev_" + "5" * 64
_SOURCE_REVISION_DIGEST = "6" * 64


def _source(path: Path, *, domain_count: int) -> Path:
    with CodeCompassDomainSupplementSourceWriter(path) as writer:
        for index in range(domain_count):
            label = f"domain-{index}"
            writer.add_domain(
                SemanticDomainIdentity(
                    domain_key=codecompass_semantic_domain_key(label),
                    domain_kind="top_level_path",
                    domain_label=label,
                ),
                source_file_count=1,
            )
        return writer.finalize()


def _materialize(
    source: Path,
    destination: Path,
    *,
    content_reader: DomainSupplementLogicalContentReader | None = None,
) -> dict[str, object]:
    return WorkerCodeCompassDomainSupplementMaterializer(
        content_reader=content_reader,
    ).materialize(
        source_path=source,
        output_path=destination,
        graph_revision=_GRAPH_REVISION,
        source_scope="repository",
        knowledge_index_id="index-domain-budget",
        source_id=f"bound-source:{_SOURCE_REVISION_ID}",
        source_revision_id=_SOURCE_REVISION_ID,
        source_revision_digest=_SOURCE_REVISION_DIGEST,
    )


def test_shared_domain_budget_is_twenty_thousand() -> None:
    assert MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS == 20_000


def test_worker_accepts_exact_domain_budget_before_publication(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source.sqlite3", domain_count=3)

    result = _materialize(
        source,
        tmp_path / "accepted.sqlite3",
        content_reader=DomainSupplementLogicalContentReader(
            max_domains=3,
        ),
    )

    assert result["domain_count"] == 3
    assert (tmp_path / "accepted.sqlite3").is_file()


def test_worker_rejects_first_domain_over_budget_before_publication(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source.sqlite3", domain_count=3)
    destination = tmp_path / "rejected.sqlite3"

    with pytest.raises(
        ValueError,
        match="codecompass_domain_supplement_domain_limit_exceeded",
    ):
        _materialize(
            source,
            destination,
            content_reader=DomainSupplementLogicalContentReader(
                max_domains=2,
            ),
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.sqlite3.*.tmp"))
