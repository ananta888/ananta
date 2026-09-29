"""Tests for PatternGateService (PAT-017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.pattern_gate_service import PatternGateService, get_pattern_gate_service


def _write(tmp_path: Path, rel: str, content: str) -> str:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return rel


# ---------------------------------------------------------------------------
# Python Strategy gate
# ---------------------------------------------------------------------------

class TestStrategyGatePython:
    def test_complete_python_strategy_passes(self, tmp_path: Path):
        protocol = _write(tmp_path, "strategy_protocol.py",
            "from typing import Protocol\nclass OrderStrategy(Protocol):\n    def execute(self, p: dict) -> dict: ...")
        context = _write(tmp_path, "strategy_context.py",
            "class OrderContext:\n    def __init__(self, s): self._s = s")
        primary = _write(tmp_path, "strategy_primary.py",
            "class PrimaryStrategy:\n    def execute(self, p): return p")
        secondary = _write(tmp_path, "strategy_secondary.py",
            "class SecondaryStrategy:\n    def execute(self, p): return p")
        test = _write(tmp_path, "test_strategy.py",
            "def test_order(): pass")

        svc = PatternGateService()
        result = svc.check(
            pattern_id="python.strategy",
            language="python",
            output_files=[protocol, context, primary, secondary, test],
            workspace_root=tmp_path,
            require_tests=True,
        )
        assert result.passed, result.failed_checks

    def test_missing_protocol_fails(self, tmp_path: Path):
        ctx = _write(tmp_path, "ctx.py", "class OrderContext: pass")
        test = _write(tmp_path, "test_strategy.py", "def test_x(): pass")
        svc = PatternGateService()
        result = svc.check(
            pattern_id="python.strategy",
            language="python",
            output_files=[ctx, test],
            workspace_root=tmp_path,
        )
        assert "has_protocol_or_abc" in result.failed_checks

    def test_missing_test_fails_when_required(self, tmp_path: Path):
        protocol = _write(tmp_path, "proto.py",
            "from typing import Protocol\nclass OrderStrategy(Protocol):\n    def execute(self, p): ...")
        ctx = _write(tmp_path, "ctx.py", "class OrderContext: pass")
        primary = _write(tmp_path, "p1.py", "class PrimaryStrategy:\n    def execute(self, p): return p")
        secondary = _write(tmp_path, "p2.py", "class SecondaryStrategy:\n    def execute(self, p): return p")
        svc = PatternGateService()
        result = svc.check(
            pattern_id="python.strategy",
            language="python",
            output_files=[protocol, ctx, primary, secondary],
            workspace_root=tmp_path,
            require_tests=True,
        )
        assert "has_test_file" in result.failed_checks

    def test_test_optional_when_not_required(self, tmp_path: Path):
        protocol = _write(tmp_path, "proto.py",
            "from typing import Protocol\nclass S(Protocol):\n    def execute(self, p): ...")
        ctx = _write(tmp_path, "ctx.py", "class OrderContext: pass")
        p1 = _write(tmp_path, "p1.py", "class A:\n    def execute(self, p): return p")
        p2 = _write(tmp_path, "p2.py", "class B:\n    def execute(self, p): return p")
        svc = PatternGateService()
        result = svc.check(
            pattern_id="python.strategy",
            language="python",
            output_files=[protocol, ctx, p1, p2],
            workspace_root=tmp_path,
            require_tests=False,
        )
        assert "has_test_file" not in result.failed_checks


# ---------------------------------------------------------------------------
# Java Strategy gate
# ---------------------------------------------------------------------------

class TestStrategyGateJava:
    def test_java_strategy_passes(self, tmp_path: Path):
        iface = _write(tmp_path, "OrderStrategy.java",
            "public interface OrderStrategy { void execute(); }")
        ctx = _write(tmp_path, "OrderContext.java",
            "public class OrderContext { private OrderStrategy s; }")
        primary = _write(tmp_path, "PrimaryStrategy.java",
            "public class PrimaryStrategy implements OrderStrategy { public void execute() {} }")
        secondary = _write(tmp_path, "SecondaryStrategy.java",
            "public class SecondaryStrategy implements OrderStrategy { public void execute() {} }")
        test = _write(tmp_path, "OrderStrategyTest.java",
            "@Test public void test() {}")

        svc = PatternGateService()
        result = svc.check(
            pattern_id="java.strategy",
            language="java",
            output_files=[iface, ctx, primary, secondary, test],
            workspace_root=tmp_path,
        )
        assert result.passed, result.failed_checks


# ---------------------------------------------------------------------------
# TypeScript Strategy gate
# ---------------------------------------------------------------------------

class TestStrategyGateTypeScript:
    def test_ts_strategy_passes(self, tmp_path: Path):
        types = _write(tmp_path, "strategy.types.ts",
            "export interface OrderStrategy { execute(p: object): object; }")
        ctx = _write(tmp_path, "context.ts",
            "import { OrderStrategy } from './strategy.types';\nexport class OrderContext { constructor(private s: OrderStrategy) {} }")
        primary = _write(tmp_path, "primary.strategy.ts",
            "export class PrimaryStrategy implements OrderStrategy { execute(p: object) { return p; } }")
        test = _write(tmp_path, "context.test.ts",
            "test('strategy', () => { expect(true).toBe(true); });")

        svc = PatternGateService()
        result = svc.check(
            pattern_id="ts.strategy",
            language="typescript",
            output_files=[types, ctx, primary, test],
            workspace_root=tmp_path,
        )
        assert result.passed, result.failed_checks


# ---------------------------------------------------------------------------
# Generic / unknown pattern fallback
# ---------------------------------------------------------------------------

class TestGenericGate:
    def test_unknown_pattern_checks_files_exist(self, tmp_path: Path):
        f1 = _write(tmp_path, "output.py", "# generated")
        svc = PatternGateService()
        result = svc.check(
            pattern_id="unknown.pattern",
            language="python",
            output_files=[f1],
            workspace_root=tmp_path,
        )
        assert result.passed

    def test_unknown_pattern_missing_file_fails(self, tmp_path: Path):
        svc = PatternGateService()
        result = svc.check(
            pattern_id="unknown.pattern",
            language="python",
            output_files=["missing_file.py"],
            workspace_root=tmp_path,
        )
        assert not result.passed

    def test_to_dict_structure(self, tmp_path: Path):
        svc = PatternGateService()
        result = svc.check(
            pattern_id="unknown.pattern",
            language="python",
            output_files=[],
            workspace_root=tmp_path,
        )
        d = result.to_dict()
        assert "pattern_id" in d
        assert "passed" in d
        assert "details" in d
        assert "remediation_hint" in d


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_get_pattern_gate_service_singleton():
    s1 = get_pattern_gate_service()
    s2 = get_pattern_gate_service()
    assert s1 is s2
