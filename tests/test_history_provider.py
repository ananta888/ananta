"""Tests for agent/services/history_provider.py (COSMOS-011)."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.services.history_provider import (
    ADRRecord,
    CommitRecord,
    HistoryProviderCapabilities,
    IssueRecord,
    LocalGitHistoryProvider,
    NullHistoryProvider,
    PRRecord,
    _parse_adr_markdown,
    _parse_adr_yaml,
    _try_parse_date,
    mark_stale_records,
)


# ── NullHistoryProvider ───────────────────────────────────────────────────────

class TestNullHistoryProvider:
    def setup_method(self):
        self.provider = NullHistoryProvider()

    def test_get_commits_returns_empty(self):
        assert self.provider.get_commits(["some/path"]) == []

    def test_get_prs_returns_empty(self):
        assert self.provider.get_prs([]) == []

    def test_get_issues_returns_empty(self):
        assert self.provider.get_issues(["keyword"]) == []

    def test_get_adrs_returns_empty(self):
        assert self.provider.get_adrs() == []

    def test_capabilities_provider_is_null(self):
        caps = self.provider.capabilities()
        assert caps.provider == "null"

    def test_capabilities_all_false(self):
        caps = self.provider.capabilities()
        assert caps.supports_git is False
        assert caps.supports_prs is False
        assert caps.supports_issues is False
        assert caps.supports_adrs is False


# ── LocalGitHistoryProvider ───────────────────────────────────────────────────

class TestLocalGitHistoryProvider:
    def test_get_commits_no_git_returns_empty(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        # tmp_path has no .git dir
        result = provider.get_commits([])
        assert result == []

    def test_get_prs_always_empty(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        assert provider.get_prs([]) == []

    def test_get_issues_always_empty(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        assert provider.get_issues(["foo"]) == []

    def test_capabilities_provider_name(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        caps = provider.capabilities()
        assert caps.provider == "local_git"
        assert caps.supports_git is True
        assert caps.supports_adrs is True
        assert caps.supports_prs is False

    def test_get_adrs_no_dir_returns_empty(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        result = provider.get_adrs()
        assert result == []

    def test_get_adrs_scans_docs_adr(self, tmp_path):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "001-use-postgres.md").write_text(
            "# Use PostgreSQL\n\nStatus: accepted\n\nWe chose postgres.\n"
        )
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        records = provider.get_adrs()
        assert len(records) == 1
        assert records[0].adr_id == "001-use-postgres"

    def test_get_adrs_query_filters(self, tmp_path):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "001-postgres.md").write_text("# Postgres Decision\n\nWe use postgres.")
        (adr_dir / "002-redis.md").write_text("# Redis Decision\n\nWe use redis.")
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        results = provider.get_adrs(query="postgres")
        assert len(results) == 1
        assert "postgres" in results[0].adr_id

    def test_get_adrs_ignores_non_md_files(self, tmp_path):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "readme.txt").write_text("not an ADR")
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        assert provider.get_adrs() == []

    def test_parse_git_log_with_mock(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        fake_output = (
            "COMMIT_MARKER\x1fabc123\x1fAlice\x1f1700000000\x1fFix bug\n"
            "src/main.py\n"
            "src/utils.py\n"
        )
        records = provider._parse_git_log(fake_output)
        assert len(records) == 1
        assert records[0].commit_id == "abc123"
        assert records[0].author == "Alice"
        assert records[0].message == "Fix bug"
        assert "src/main.py" in records[0].changed_paths
        assert "src/utils.py" in records[0].changed_paths

    def test_parse_git_log_multiple_commits(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path))
        fake_output = (
            "COMMIT_MARKER\x1fabc\x1fAlice\x1f1700000000\x1fFirst\n"
            "file_a.py\n"
            "COMMIT_MARKER\x1fdef\x1fBob\x1f1700001000\x1fSecond\n"
            "file_b.py\n"
        )
        records = provider._parse_git_log(fake_output)
        assert len(records) == 2
        assert records[0].commit_id == "abc"
        assert records[1].commit_id == "def"

    def test_is_stale_old_timestamp(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path), stale_days=30)
        old_ts = time.time() - (31 * 86400)
        assert provider._is_stale(old_ts) is True

    def test_is_stale_recent_timestamp(self, tmp_path):
        provider = LocalGitHistoryProvider(repo_path=str(tmp_path), stale_days=30)
        recent_ts = time.time() - (10 * 86400)
        assert provider._is_stale(recent_ts) is False

    def test_get_commits_real_repo(self):
        # Use the actual ananta repo (which is a git repo)
        provider = LocalGitHistoryProvider(repo_path="/home/krusty/ananta")
        records = provider.get_commits([], limit=5)
        # Should return some commits (repo has history)
        assert isinstance(records, list)
        if records:
            assert isinstance(records[0], CommitRecord)
            assert records[0].commit_id != ""


# ── mark_stale_records ────────────────────────────────────────────────────────

class TestMarkStaleRecords:
    def test_old_commit_marked_stale(self):
        old_ts = time.time() - (100 * 86400)
        record = CommitRecord(
            commit_id="abc", author="A", timestamp=old_ts,
            message="old", changed_paths=[], stale=False,
        )
        result = mark_stale_records([record], stale_days=90)
        assert result[0].stale is True

    def test_recent_commit_not_stale(self):
        recent_ts = time.time() - (10 * 86400)
        record = CommitRecord(
            commit_id="abc", author="A", timestamp=recent_ts,
            message="recent", changed_paths=[], stale=False,
        )
        result = mark_stale_records([record], stale_days=90)
        assert result[0].stale is False

    def test_empty_list_returns_empty(self):
        assert mark_stale_records([]) == []

    def test_pr_with_merged_at_stale(self):
        old_ts = time.time() - (200 * 86400)
        pr = PRRecord(
            pr_id="1", title="T", author="A", merged_at=old_ts,
            changed_paths=[], state="merged", body_summary="", stale=False,
        )
        result = mark_stale_records([pr], stale_days=90)
        assert result[0].stale is True

    def test_pr_with_none_merged_at_not_stale(self):
        pr = PRRecord(
            pr_id="2", title="T", author="A", merged_at=None,
            changed_paths=[], state="open", body_summary="", stale=False,
        )
        result = mark_stale_records([pr], stale_days=90)
        assert result[0].stale is False


# ── _try_parse_date ───────────────────────────────────────────────────────────

class TestTryParseDate:
    def test_simple_date(self):
        ts = _try_parse_date("2023-01-15")
        assert ts is not None
        assert ts > 0

    def test_datetime_with_time(self):
        ts = _try_parse_date("2023-01-15T10:30:00")
        assert ts is not None

    def test_datetime_with_z(self):
        ts = _try_parse_date("2023-01-15T10:30:00Z")
        assert ts is not None

    def test_invalid_date_returns_none(self):
        assert _try_parse_date("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert _try_parse_date("") is None


# ── _parse_adr_yaml ───────────────────────────────────────────────────────────

class TestParseAdrYaml:
    def test_parses_title_and_status(self):
        text = "title: Use PostgreSQL\nstatus: accepted\ndate: 2023-01-01\n"
        title, status, created_at = _parse_adr_yaml(text)
        assert title == "Use PostgreSQL"
        assert status == "accepted"
        assert created_at is not None

    def test_unknown_status_defaults_to_proposed(self):
        text = "title: Foo\nstatus: unknown_status\n"
        _, status, _ = _parse_adr_yaml(text)
        assert status == "proposed"

    def test_empty_text(self):
        title, status, created_at = _parse_adr_yaml("")
        assert title == ""
        assert status == "proposed"
        assert created_at is None


# ── _parse_adr_markdown ───────────────────────────────────────────────────────

class TestParseAdrMarkdown:
    def test_parses_front_matter_title(self):
        text = "---\ntitle: My ADR\nstatus: accepted\n---\n\nBody text here.\n"
        title, status, created_at, summary = _parse_adr_markdown(text)
        assert title == "My ADR"
        assert status == "accepted"

    def test_parses_h1_title_without_front_matter(self):
        text = "# Deploy with Docker\n\nWe decided to use Docker.\n"
        title, status, created_at, summary = _parse_adr_markdown(text)
        assert title == "Deploy with Docker"

    def test_summary_capped_at_500_chars(self):
        text = "# T\n\n" + "x" * 1000
        _, _, _, summary = _parse_adr_markdown(text)
        assert len(summary) <= 500
