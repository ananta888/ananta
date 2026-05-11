"""AFH-T020: Audit event constant tests for artifact-first transitions."""
from __future__ import annotations

from agent.common.audit import (
    AUDIT_WORKER_HANDOFF_CREATED,
    AUDIT_ARTIFACT_MANIFEST_COLLECTED,
    AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED,
    AUDIT_ARTIFACT_COMPLETION_DECIDED,
    AUDIT_TASK_FINALIZED_FROM_ARTIFACTS,
    AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED,
    AUDIT_ARTIFACT_RECONCILIATION_APPLIED,
)


class TestArtifactFirstAuditEvents:
    def test_all_required_audit_event_constants_exist(self) -> None:
        """All artifact-first audit event constants must be defined."""
        events = [
            AUDIT_WORKER_HANDOFF_CREATED,
            AUDIT_ARTIFACT_MANIFEST_COLLECTED,
            AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED,
            AUDIT_ARTIFACT_COMPLETION_DECIDED,
            AUDIT_TASK_FINALIZED_FROM_ARTIFACTS,
            AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED,
            AUDIT_ARTIFACT_RECONCILIATION_APPLIED,
        ]
        for event in events:
            assert isinstance(event, str) and event, f"Audit event constant must be non-empty string: {event!r}"

    def test_event_names_are_snake_case(self) -> None:
        events = [
            AUDIT_WORKER_HANDOFF_CREATED,
            AUDIT_ARTIFACT_MANIFEST_COLLECTED,
            AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED,
            AUDIT_ARTIFACT_COMPLETION_DECIDED,
            AUDIT_TASK_FINALIZED_FROM_ARTIFACTS,
            AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED,
            AUDIT_ARTIFACT_RECONCILIATION_APPLIED,
        ]
        for event in events:
            assert event == event.lower(), f"Audit event must be lowercase: {event!r}"
            assert " " not in event, f"Audit event must not contain spaces: {event!r}"

    def test_advisory_parse_failed_ignored_event_name(self) -> None:
        """Key event: advisory parse failure must be separately recorded."""
        assert AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED == "advisory_json_parse_failed_ignored"

    def test_reconciliation_event_exists(self) -> None:
        """Manual reconciliation must have its own audit event."""
        assert AUDIT_ARTIFACT_RECONCILIATION_APPLIED == "artifact_reconciliation_applied"
