from __future__ import annotations

import pytest

from agent.repositories.local_tool_training import SqliteLocalToolTrainingRepository
from agent.services.local_tool_interaction_collector import LocalToolInteractionCollector
from agent.services.local_tool_training_redaction import (
    LocalToolTrainingRedactionPolicy,
    ToolTrainingRedactionError,
)
from ananta_contracts.local_tool_training import IndependentToolOutcome, ToolDecision


def test_collector_requires_independent_outcome_and_derives_separate_label(tmp_path) -> None:
    repository = SqliteLocalToolTrainingRepository(
        tmp_path / "training.sqlite3",
        redaction=LocalToolTrainingRedactionPolicy(),
    )
    repository.save_outcome(
        IndependentToolOutcome(
            interaction_id="interaction-1",
            decision=ToolDecision(tool_name="read_file", arguments={"path_hint": "src/main.py"}),
            outcome_source="authorized_execution",
            execution_status="arguments_rejected",
            evidence_sha256="a" * 64,
        )
    )
    audits = []
    collector = LocalToolInteractionCollector(
        repository=repository,
        audit_sink=lambda action, facts: audits.append((action, dict(facts))),
    )

    record = collector.collect(
        interaction_id="interaction-1",
        observed_at="2026-08-01T00:00:00Z",
        request_class="repository_read",
        expected_schema={"path": {"type": "string"}},
        candidate_tool="read_file",
        candidate_arguments={"path_hint": "src/main.py"},
    )

    assert record.outcome_label == "invalid_arguments"
    assert repository.records() == (record,)
    assert record.outcome_evidence_sha256 == "a" * 64
    assert "arguments" not in audits[0][1]
    with pytest.raises(ValueError, match="tool_training_outcome_not_independent"):
        collector.collect(
            interaction_id="interaction-2",
            observed_at="2026-08-01T00:00:00Z",
            request_class="repository_read",
            expected_schema={},
            candidate_tool=None,
            candidate_arguments={},
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"api_key": "sk-this-must-never-persist"},
        {"note": "authorization: Bearer very-secret-token-value"},
        {"email": "alice@example.org"},
        {"private_key": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_secrets_credentials_and_pii_are_blocked_before_persist_or_audit(arguments, tmp_path) -> None:
    repository = SqliteLocalToolTrainingRepository(
        tmp_path / "training.sqlite3",
        redaction=LocalToolTrainingRedactionPolicy(),
    )
    repository.save_outcome(
        IndependentToolOutcome(
            interaction_id="interaction-secret",
            decision=ToolDecision(tool_name="read_file", arguments={}),
            outcome_source="human_review",
            execution_status="completed",
            evidence_sha256="b" * 64,
        )
    )
    audits = []
    collector = LocalToolInteractionCollector(
        repository=repository,
        audit_sink=lambda action, facts: audits.append((action, facts)),
    )

    with pytest.raises(ToolTrainingRedactionError):
        collector.collect(
            interaction_id="interaction-secret",
            observed_at="2026-08-01T00:00:00Z",
            request_class="repository_read",
            expected_schema={},
            candidate_tool="read_file",
            candidate_arguments=arguments,
        )

    assert repository.records() == ()
    assert audits == []


def test_independent_outcome_secrets_are_rejected_before_repository_persistence(tmp_path) -> None:
    repository = SqliteLocalToolTrainingRepository(
        tmp_path / "training.sqlite3",
        redaction=LocalToolTrainingRedactionPolicy(),
    )
    outcome = IndependentToolOutcome(
        interaction_id="interaction-secret-outcome",
        decision=ToolDecision(tool_name="read_file", arguments={"password": "never-store-this"}),
        outcome_source="authorized_execution",
        execution_status="completed",
        evidence_sha256="c" * 64,
    )

    with pytest.raises(ToolTrainingRedactionError, match="secret_field_blocked"):
        repository.save_outcome(outcome)

    assert repository.get_outcome("interaction-secret-outcome") is None
