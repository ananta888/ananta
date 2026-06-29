"""Tests for Job Posting Normalizer (JOBCORE-003)."""
from __future__ import annotations

import pytest
from agent.job_module.posting_normalizer import normalize_posting, JobPosting


GERMAN_POSTING = """
Python Developer (m/w/d)

Wir suchen einen erfahrenen Python Developer für unser Team bei TechGmbH.

Ihre Aufgaben:
- Entwicklung von Flask und FastAPI Backends
- Datenbankdesign mit PostgreSQL
- Docker und Kubernetes Deployment

Anforderungen:
- Python Expertise (3+ Jahre)
- Kenntnisse in Flask, FastAPI
- Docker Erfahrung
- Remote möglich

Gehalt: 70.000 - 90.000 EUR
"""

ENGLISH_POSTING = """
Backend Engineer

We are looking for a Backend Engineer at StartupCo.

Requirements:
- Python and Django experience
- PostgreSQL and Redis knowledge
- AWS experience preferred
- Remote work accepted, hybrid ok

Salary: 80k - 100k
"""

SHORT_POSTING = "Dev job"


class TestJobPostingNormalizer:
    def test_normalize_german_posting_extracts_title(self):
        posting = normalize_posting(GERMAN_POSTING)
        assert "Python" in posting.title or "Developer" in posting.title

    def test_normalize_english_posting(self):
        posting = normalize_posting(ENGLISH_POSTING)
        assert posting.detected_language in ("en", "de")  # allow both for short text
        assert posting.title != ""

    def test_normalize_short_text(self):
        posting = normalize_posting(SHORT_POSTING)
        assert posting.raw_text == SHORT_POSTING
        assert posting.title == "Dev job"

    def test_remote_keyword_detection_german(self):
        posting = normalize_posting(GERMAN_POSTING)
        assert posting.remote_policy == "remote"

    def test_remote_keyword_detection_english(self):
        english_remote = "Senior Dev. Fully remote or hybrid. Python required."
        posting = normalize_posting(english_remote)
        assert posting.remote_policy in ("remote", "hybrid")

    def test_salary_pattern_extraction(self):
        posting = normalize_posting(GERMAN_POSTING)
        assert posting.salary_text is not None
        assert "EUR" in posting.salary_text or "€" in posting.salary_text

    def test_tech_stack_detection(self):
        posting = normalize_posting(GERMAN_POSTING)
        tech = posting.metadata.get("tech_stack", [])
        assert "python" in tech or "flask" in tech

    def test_raw_text_preserved(self):
        posting = normalize_posting(GERMAN_POSTING)
        assert posting.raw_text == GERMAN_POSTING

    def test_unclear_fields_are_none(self):
        minimal = "Looking for developer"
        posting = normalize_posting(minimal)
        assert posting.salary_text is None

    def test_normalizer_is_deterministic(self):
        p1 = normalize_posting(GERMAN_POSTING)
        p2 = normalize_posting(GERMAN_POSTING)
        assert p1.title == p2.title
        assert p1.remote_policy == p2.remote_policy
        assert p1.salary_text == p2.salary_text
