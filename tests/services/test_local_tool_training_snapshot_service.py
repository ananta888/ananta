from __future__ import annotations

import json

import pytest

from agent.repositories.local_tool_training import SqliteLocalToolTrainingRepository
from agent.services.local_tool_interaction_collector import LocalToolInteractionCollector
from agent.services.local_tool_training_redaction import LocalToolTrainingRedactionPolicy
from agent.services.local_tool_training_snapshot_service import (
    LocalToolTrainingSnapshotService,
    ToolTrainingSnapshotError,
)
from ananta_contracts.local_tool_training import IndependentToolOutcome, ToolDecision


def _record(tmp_path, interaction_id: str, observed_at: str, request_class: str):
    repository = SqliteLocalToolTrainingRepository(
        tmp_path / f"{interaction_id}.sqlite3",
        redaction=LocalToolTrainingRedactionPolicy(),
    )
    repository.save_outcome(
        IndependentToolOutcome(
            interaction_id=interaction_id,
            decision=ToolDecision(tool_name="lookup", arguments={"query": request_class}),
            outcome_source="golden_fixture",
            execution_status="completed",
            evidence_sha256="a" * 64,
        )
    )
    collector = LocalToolInteractionCollector(repository=repository)
    record = collector.collect(
        interaction_id=interaction_id,
        observed_at=observed_at,
        request_class=request_class,
        expected_schema={"value": {"type": "string"}},
        candidate_tool="lookup",
        candidate_arguments={"query": request_class},
    )
    return collector, record


def test_snapshot_is_canonical_immutable_time_partitioned_and_provenance_bound(tmp_path) -> None:
    first_collector, train = _record(tmp_path, "i-train", "2026-01-01T00:00:00Z", "train_class")
    _, validation = _record(tmp_path, "i-validation", "2026-02-01T00:00:00Z", "validation_class")
    _, test = _record(tmp_path, "i-test", "2026-03-01T00:00:00Z", "test_class")
    service = LocalToolTrainingSnapshotService(
        storage_root=tmp_path,
        allowed_source_ids=["SRC_approved:1"],
        allowed_run_ids=["RUN_approved:1"],
        collector_policy_sha256=first_collector.policy_digest,
    )

    snapshot = service.create(
        dataset_id="dataset-1",
        records=[test, train, validation],
        train_end="2026-01-31T23:59:59Z",
        validation_end="2026-02-28T23:59:59Z",
        test_end="2026-03-31T23:59:59Z",
        source_ids=["SRC_approved:1"],
        run_ids=["RUN_approved:1"],
        collector_policy_sha256=first_collector.policy_digest,
        redaction_policy_sha256=train.redaction_policy_sha256,
        created_at="2026-04-01T00:00:00Z",
    )

    root = tmp_path / snapshot.snapshot_id
    assert json.loads((root / "manifest.json").read_text()) == snapshot.to_wire()
    assert snapshot.train_records == snapshot.validation_records == snapshot.test_records == 1
    assert (
        service.create(
            dataset_id="dataset-1",
            records=[train, validation, test],
            train_end="2026-01-31T23:59:59Z",
            validation_end="2026-02-28T23:59:59Z",
            test_end="2026-03-31T23:59:59Z",
            source_ids=["SRC_approved:1"],
            run_ids=["RUN_approved:1"],
            collector_policy_sha256=first_collector.policy_digest,
            redaction_policy_sha256=train.redaction_policy_sha256,
            created_at="2026-04-01T00:00:00Z",
        )
        == snapshot
    )


def test_snapshot_rejects_unknown_provenance_and_cross_partition_similarity(tmp_path) -> None:
    collector, train = _record(tmp_path, "i-1", "2026-01-01T00:00:00Z", "same_class")
    _, validation = _record(tmp_path, "i-2", "2026-02-01T00:00:00Z", "same_class")
    _, test = _record(tmp_path, "i-3", "2026-03-01T00:00:00Z", "other_class")
    service = LocalToolTrainingSnapshotService(
        storage_root=tmp_path,
        allowed_source_ids=["SRC_approved:1"],
        allowed_run_ids=["RUN_approved:1"],
        collector_policy_sha256=collector.policy_digest,
    )
    kwargs = dict(
        dataset_id="dataset-1",
        records=[train, validation, test],
        train_end="2026-01-31T23:59:59Z",
        validation_end="2026-02-28T23:59:59Z",
        test_end="2026-03-31T23:59:59Z",
        source_ids=["SRC_approved:1"],
        run_ids=["RUN_approved:1"],
        collector_policy_sha256=collector.policy_digest,
        redaction_policy_sha256=train.redaction_policy_sha256,
        created_at="2026-04-01T00:00:00Z",
    )

    with pytest.raises(ToolTrainingSnapshotError, match="snapshot_similarity_leakage"):
        service.create(**kwargs)
    with pytest.raises(ToolTrainingSnapshotError, match="provenance_unverified"):
        service.create(**{**kwargs, "source_ids": ["SRC_unknown:1"]})
