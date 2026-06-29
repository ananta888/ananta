"""Tests for Job Fit Scoring (JOBCORE-004)."""
from __future__ import annotations

import pytest
from agent.job_module.fit_scoring import EvidenceRef, JobFitScore, SubScore


class TestJobFitScore:
    def test_fit_score_creation(self):
        score = JobFitScore(case_id="case-1")
        assert score.case_id == "case-1"
        assert score.source == "ai"
        assert score.final_score is None
        assert score.id is not None

    def test_final_score_computed_from_subscores(self):
        score = JobFitScore(
            case_id="case-1",
            technical_fit=SubScore(score=8.0, explanation="Good Python"),
            domain_fit=SubScore(score=6.0, explanation="Partial domain match"),
            location_fit=SubScore(score=9.0, explanation="Remote friendly"),
        )
        final = score.compute_final_score()
        assert final is not None
        # Average of 8, 6, 9 = 7.67
        assert abs(final - (8 + 6 + 9) / 3) < 0.01

    def test_manual_override_takes_precedence(self):
        score = JobFitScore(
            case_id="case-1",
            technical_fit=SubScore(score=5.0, explanation="Low"),
            manual_override=9.0,
            manual_override_reason="Great company culture",
        )
        final = score.compute_final_score()
        assert final == 9.0

    def test_unknown_score_when_missing_data(self):
        score = JobFitScore(case_id="case-1")
        assert score.compute_final_score() is None
        assert score.technical_fit is None

    def test_evidence_refs_structure(self):
        ev = EvidenceRef(
            artifact_id="art-1",
            field_path="tech_stack[0]",
            quote_hash="abc123",
            explanation="Python in tech_stack",
        )
        subscore = SubScore(
            score=8.5,
            explanation="Strong technical match",
            evidence=[ev],
        )
        assert subscore.evidence[0].artifact_id == "art-1"
        assert subscore.evidence[0].explanation == "Python in tech_stack"

    def test_ai_vs_manual_source_distinct(self):
        ai_score = JobFitScore(case_id="case-1", source="ai")
        manual_score = JobFitScore(case_id="case-1", source="manual")
        assert ai_score.source == "ai"
        assert manual_score.source == "manual"

    def test_subscore_with_none_score(self):
        score = SubScore(score=None, explanation="Not enough data to evaluate")
        assert score.score is None
        job_score = JobFitScore(
            case_id="case-1",
            seniority_fit=SubScore(score=None, explanation="Unknown"),
            technical_fit=SubScore(score=7.0, explanation="OK"),
        )
        # Only technical_fit contributes (seniority_fit.score is None)
        final = job_score.compute_final_score()
        assert final == 7.0
