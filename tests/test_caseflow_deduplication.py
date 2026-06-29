"""Tests for CaseFlow Deduplication (DISCOVERY-003)."""
from __future__ import annotations

import pytest
from agent.caseflow.deduplication import (
    DuplicateCheckResult,
    check_duplicate,
    compute_fingerprint,
)
from agent.caseflow.discovery import DiscoveryResult


def _make_result(title: str, url: str | None = None, raw_text: str = "", run_id: str = "r1") -> DiscoveryResult:
    r = DiscoveryResult(run_id=run_id, result_type="job_posting", title=title, source_name="test", source_url=url, raw_text=raw_text)
    r.fingerprint = compute_fingerprint(r.result_type, r.title, r.source_url, r.normalized_payload)
    return r


class TestDeduplication:
    def test_exact_url_duplicate_detected(self):
        r1 = _make_result("Python Dev", url="https://example.com/jobs/1")
        r2 = _make_result("Python Dev AT ACME", url="https://example.com/jobs/1")
        result = check_duplicate(r2, [r1])
        assert result.is_duplicate is True
        assert result.method == "exact_url"
        assert result.duplicate_of == r1.id

    def test_same_fingerprint_duplicate_detected(self):
        r1 = _make_result("Python Developer", url="https://example.com/job1")
        r2 = _make_result("Python Developer", url="https://example.com/job1")
        # Same title + URL → same fingerprint
        result = check_duplicate(r2, [r1])
        assert result.is_duplicate is True

    def test_near_duplicate_by_jaccard(self):
        # Build a large shared vocabulary so a few extra words stay below 15% of the union.
        # Use different titles so fingerprints differ but raw_text stays very similar.
        shared_words = " ".join([f"token{i}" for i in range(200)])
        shared_words += " python flask postgresql docker kubernetes aws backend developer"
        r1 = _make_result("Senior Python Dev Alpha", raw_text=shared_words, url="https://a.com/1")
        # Different title (different fingerprint), only one extra raw_text word
        r2 = _make_result("Senior Python Dev Beta", raw_text=shared_words + " xunique1", url="https://b.com/2")
        result = check_duplicate(r2, [r1])
        assert result.is_duplicate is True
        assert result.method == "near_duplicate"
        assert result.similarity >= 0.85

    def test_non_duplicate_different_content(self):
        r1 = _make_result("Python Developer", url="https://company-a.com/jobs/1",
                          raw_text="Python flask experience required")
        r2 = _make_result("Java Developer", url="https://company-b.com/jobs/2",
                          raw_text="Java Spring Boot enterprise application development")
        result = check_duplicate(r2, [r1])
        assert result.is_duplicate is False

    def test_fingerprint_stable_for_same_input(self):
        fp1 = compute_fingerprint("job_posting", "Python Dev", "https://example.com/1", {})
        fp2 = compute_fingerprint("job_posting", "Python Dev", "https://example.com/1", {})
        assert fp1 == fp2
        assert len(fp1) == 40  # SHA1 hex length

    def test_ignored_result_remembered(self):
        r = _make_result("Test Job")
        r.ignored = True
        assert r.ignored is True
        # Check that ignored results can still be identified
        other = _make_result("Other Job")
        result = check_duplicate(other, [r])
        assert result.is_duplicate is False  # different content
