"""Tests for PatternArtifactService (PAT-016)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.pattern_artifact_service import (
    PatternArtifactService,
    make_plan_hash,
    get_pattern_artifact_service,
)


class TestMakePlanHash:
    def test_stable_for_same_inputs(self):
        h1 = make_plan_hash("python.strategy", "python", {"context_class": "Order"})
        h2 = make_plan_hash("python.strategy", "python", {"context_class": "Order"})
        assert h1 == h2

    def test_changes_with_different_params(self):
        h1 = make_plan_hash("python.strategy", "python", {"context_class": "Order"})
        h2 = make_plan_hash("python.strategy", "python", {"context_class": "Payment"})
        assert h1 != h2

    def test_changes_with_different_language(self):
        h1 = make_plan_hash("strategy", "python", {})
        h2 = make_plan_hash("strategy", "java", {})
        assert h1 != h2


class TestPatternArtifactService:
    def test_record_and_get(self, tmp_path: Path):
        svc = PatternArtifactService(artifacts_root=tmp_path)
        plan_hash = make_plan_hash("python.strategy", "python", {"context_class": "Order"})
        record = svc.record(
            pattern_id="python.strategy",
            language="python",
            plan_hash=plan_hash,
            template_hash="abc123",
            generated_files=[
                {"role": "protocol", "path": "strategy_protocol.py", "sha256": "aaa", "size_bytes": 100},
                {"role": "test", "path": "test_strategy.py", "sha256": "bbb", "size_bytes": 200},
            ],
        )
        assert record.artifact_id == f"pat-{plan_hash[:12]}"
        assert record.pattern_id == "python.strategy"
        assert len(record.generated_files) == 2

        fetched = svc.get(plan_hash)
        assert fetched is not None
        assert fetched.artifact_id == record.artifact_id
        assert len(fetched.generated_files) == 2

    def test_idempotent_write(self, tmp_path: Path):
        svc = PatternArtifactService(artifacts_root=tmp_path)
        plan_hash = make_plan_hash("java.strategy", "java", {"context_class": "Cart", "package_name": "com.example"})
        svc.record(
            pattern_id="java.strategy",
            language="java",
            plan_hash=plan_hash,
            template_hash="t1",
            generated_files=[],
        )
        svc.record(
            pattern_id="java.strategy",
            language="java",
            plan_hash=plan_hash,
            template_hash="t1",
            generated_files=[],
        )
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_get_missing_returns_none(self, tmp_path: Path):
        svc = PatternArtifactService(artifacts_root=tmp_path)
        assert svc.get("deadbeef1234") is None

    def test_to_dict_round_trip(self, tmp_path: Path):
        svc = PatternArtifactService(artifacts_root=tmp_path)
        plan_hash = make_plan_hash("ts.strategy", "typescript", {"context_class": "Checkout"})
        record = svc.record(
            pattern_id="ts.strategy",
            language="typescript",
            plan_hash=plan_hash,
            template_hash="tmpl42",
            generated_files=[{"role": "types", "path": "types.ts", "sha256": "abc", "size_bytes": 50}],
            warnings=["unused param: module_name"],
        )
        d = record.to_dict()
        assert d["schema"] == "pattern_artifact.v1"
        assert d["pattern_id"] == "ts.strategy"
        assert d["warnings"] == ["unused param: module_name"]
        assert len(d["generated_files"]) == 1

    def test_singleton_uses_default_root(self):
        svc = get_pattern_artifact_service()
        assert isinstance(svc, PatternArtifactService)

    def test_custom_root_not_cached(self, tmp_path: Path):
        svc = get_pattern_artifact_service(artifacts_root=tmp_path)
        assert str(svc._root) == str(tmp_path)
