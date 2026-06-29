"""Tests for CaseFlow Artifact System (CASECORE-003)."""
from __future__ import annotations

import pytest
from agent.caseflow.artifacts import ArtifactKind, ArtifactStatus, CaseArtifact
from agent.caseflow.privacy import classify_artifact_sensitivity


class TestCaseArtifact:
    def test_artifact_creation_minimal(self):
        a = CaseArtifact(case_id="case-1", artifact_type="notes", title="My Notes")
        assert a.case_id == "case-1"
        assert a.artifact_type == "notes"
        assert a.status == ArtifactStatus.draft
        assert a.id is not None

    def test_artifact_has_trace_id(self):
        a = CaseArtifact(
            case_id="case-1",
            artifact_type="cover_letter",
            title="Anschreiben v1",
            trace_id="trace-abc-123",
            agent_run_id="run-xyz",
        )
        assert a.trace_id == "trace-abc-123"
        assert a.agent_run_id == "run-xyz"

    def test_ai_artifact_vs_manual(self):
        ai_artifact = CaseArtifact(
            case_id="case-1", artifact_type="cover_letter",
            title="KI-Anschreiben", source="agent", trace_id="trace-1"
        )
        manual_artifact = CaseArtifact(
            case_id="case-1", artifact_type="cv",
            title="Mein CV", source="manual"
        )
        assert ai_artifact.source == "agent"
        assert manual_artifact.source == "manual"
        assert ai_artifact.trace_id is not None
        assert manual_artifact.trace_id is None

    def test_artifact_versioning(self):
        v1 = CaseArtifact(case_id="c", artifact_type="cover_letter", title="CL v1", version=1)
        v2 = CaseArtifact(
            case_id="c", artifact_type="cover_letter", title="CL v2",
            version=2, previous_artifact_id=v1.id,
            version_group_id="group-cl-1"
        )
        assert v2.version == 2
        assert v2.previous_artifact_id == v1.id
        # v1 is not overwritten
        assert v1.version == 1

    def test_artifact_content_text_optional(self):
        a = CaseArtifact(case_id="c", artifact_type="file", title="Big PDF",
                         content_ref="/files/my.pdf", content_text=None)
        assert a.content_text is None
        assert a.content_ref is not None

    def test_sensitive_artifact_classification(self):
        cv = CaseArtifact(case_id="c", artifact_type="cv", title="CV")
        notes = CaseArtifact(case_id="c", artifact_type="personal_notes", title="Notes")
        posting = CaseArtifact(case_id="c", artifact_type="job_posting", title="Posting")
        assert classify_artifact_sensitivity(cv) is True
        assert classify_artifact_sensitivity(notes) is True
        assert classify_artifact_sensitivity(posting) is False
