"""Tests for agent/services/work_context_port.py (COSMOS-012)."""
from __future__ import annotations

import pytest

from agent.services.work_context_port import (
    FileRange,
    NullWorkContext,
    SECRET_PATH_PATTERNS,
    StaticWorkContext,
    WorkContext,
    WorkContextRankingBoost,
    WorkContextSnapshot,
)


# ── NullWorkContext ───────────────────────────────────────────────────────────

class TestNullWorkContext:
    def setup_method(self):
        self.ctx = NullWorkContext()

    def test_is_available_false(self):
        assert self.ctx.is_available() is False

    def test_get_snapshot_returns_snapshot(self):
        snap = self.ctx.get_snapshot()
        assert isinstance(snap, WorkContextSnapshot)

    def test_snapshot_open_files_empty(self):
        snap = self.ctx.get_snapshot()
        assert snap.open_files == []

    def test_snapshot_active_file_none(self):
        snap = self.ctx.get_snapshot()
        assert snap.active_file is None

    def test_snapshot_source_is_null(self):
        snap = self.ctx.get_snapshot()
        assert snap.source == "null"

    def test_snapshot_dirty_files_empty(self):
        snap = self.ctx.get_snapshot()
        assert snap.dirty_files == []

    def test_snapshot_dirty_secret_files_empty(self):
        snap = self.ctx.get_snapshot()
        assert snap.dirty_secret_files == []

    def test_snapshot_selection_none(self):
        snap = self.ctx.get_snapshot()
        assert snap.selection is None

    def test_snapshot_active_branch_none(self):
        snap = self.ctx.get_snapshot()
        assert snap.active_branch is None


# ── StaticWorkContext ─────────────────────────────────────────────────────────

class TestStaticWorkContext:
    def _make_snapshot(self, **kwargs) -> WorkContextSnapshot:
        defaults = dict(
            open_files=[], active_file=None, selection=None,
            active_branch=None, dirty_files=[], dirty_secret_files=[],
            source="manual",
        )
        defaults.update(kwargs)
        return WorkContextSnapshot(**defaults)

    def test_is_available_true(self):
        snap = self._make_snapshot()
        ctx = StaticWorkContext(snap)
        assert ctx.is_available() is True

    def test_get_snapshot_returns_same_object(self):
        snap = self._make_snapshot(active_file="foo.py")
        ctx = StaticWorkContext(snap)
        assert ctx.get_snapshot() is snap

    def test_active_file_preserved(self):
        snap = self._make_snapshot(active_file="bar.py")
        ctx = StaticWorkContext(snap)
        assert ctx.get_snapshot().active_file == "bar.py"

    def test_open_files_preserved(self):
        snap = self._make_snapshot(open_files=["a.py", "b.py"])
        ctx = StaticWorkContext(snap)
        assert ctx.get_snapshot().open_files == ["a.py", "b.py"]


# ── WorkContextRankingBoost ───────────────────────────────────────────────────

class TestWorkContextRankingBoost:
    def _make_snapshot(self, **kwargs) -> WorkContextSnapshot:
        defaults = dict(
            open_files=[], active_file=None, selection=None,
            active_branch=None, dirty_files=[], dirty_secret_files=[],
            source="manual",
        )
        defaults.update(kwargs)
        return WorkContextSnapshot(**defaults)

    def setup_method(self):
        self.boost = WorkContextRankingBoost()

    def test_active_file_boost(self):
        snap = self._make_snapshot(active_file="src/main.py")
        b = self.boost.compute_boost("src/main.py", snap)
        assert b == WorkContextRankingBoost.ACTIVE_FILE_BOOST

    def test_open_file_boost_less_than_active(self):
        snap = self._make_snapshot(open_files=["src/utils.py"])
        b = self.boost.compute_boost("src/utils.py", snap)
        assert b == WorkContextRankingBoost.OPEN_FILE_BOOST
        assert b < WorkContextRankingBoost.ACTIVE_FILE_BOOST

    def test_no_match_zero_boost(self):
        snap = self._make_snapshot()
        b = self.boost.compute_boost("unrelated.py", snap)
        assert b == 0.0

    def test_boost_capped_at_active_file_boost(self):
        snap = self._make_snapshot(active_file="foo.py")
        b = self.boost.compute_boost("foo.py", snap)
        assert b <= WorkContextRankingBoost.ACTIVE_FILE_BOOST

    def test_is_secret_path_env_file(self):
        assert self.boost.is_secret_path(".env") is True

    def test_is_secret_path_pem_file(self):
        assert self.boost.is_secret_path("server.pem") is True

    def test_is_secret_path_regular_file(self):
        assert self.boost.is_secret_path("src/main.py") is False

    def test_is_secret_path_id_rsa(self):
        assert self.boost.is_secret_path("id_rsa") is True

    def test_is_secret_path_credentials_json(self):
        assert self.boost.is_secret_path("credentials.json") is True

    def test_should_warn_dirty_secret_env(self):
        assert self.boost.should_warn_dirty_secret(".env") is True

    def test_should_warn_dirty_secret_py_file(self):
        assert self.boost.should_warn_dirty_secret("main.py") is False

    def test_get_redacted_dirty_files_splits_correctly(self):
        dirty = [".env", "src/main.py", "secrets.json", "README.md"]
        clean, secret = self.boost.get_redacted_dirty_files(dirty)
        assert "src/main.py" in clean
        assert "README.md" in clean
        assert ".env" in secret
        assert "secrets.json" in secret

    def test_get_redacted_dirty_files_all_clean(self):
        dirty = ["a.py", "b.py"]
        clean, secret = self.boost.get_redacted_dirty_files(dirty)
        assert clean == ["a.py", "b.py"]
        assert secret == []

    def test_get_redacted_dirty_files_all_secret(self):
        dirty = [".env", "id_rsa"]
        clean, secret = self.boost.get_redacted_dirty_files(dirty)
        assert clean == []
        assert len(secret) == 2


# ── WorkContext Protocol ──────────────────────────────────────────────────────

class TestWorkContextProtocol:
    def test_null_satisfies_protocol(self):
        ctx = NullWorkContext()
        assert isinstance(ctx, WorkContext)

    def test_static_satisfies_protocol(self):
        snap = WorkContextSnapshot(
            open_files=[], active_file=None, selection=None,
            active_branch=None, dirty_files=[], dirty_secret_files=[],
            source="manual",
        )
        ctx = StaticWorkContext(snap)
        assert isinstance(ctx, WorkContext)


# ── FileRange ─────────────────────────────────────────────────────────────────

class TestFileRange:
    def test_basic_construction(self):
        fr = FileRange(path="src/foo.py", line_start=10, line_end=20)
        assert fr.path == "src/foo.py"
        assert fr.line_start == 10
        assert fr.line_end == 20


# ── SECRET_PATH_PATTERNS ──────────────────────────────────────────────────────

class TestSecretPathPatterns:
    def test_patterns_not_empty(self):
        assert len(SECRET_PATH_PATTERNS) > 0

    def test_env_in_patterns(self):
        assert ".env" in SECRET_PATH_PATTERNS

    def test_pem_pattern_present(self):
        assert "*.pem" in SECRET_PATH_PATTERNS
