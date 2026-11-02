"""Tests for scripts/check_cycles.py (CLN-027).

Guards the module-level import-cycle detector: synthetic cycles must be
found (including relative-import cycles), non-cycles must not, and the
baseline key format must stay stable. Fully headless and self-contained.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "check_cycles.py"


@pytest.fixture(scope="module")
def check_cycles():
    spec = importlib.util.spec_from_file_location("check_cycles_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(root: Path, module: str, body: str) -> None:
    path = root / (module.replace(".", "/") + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _cycle_groups(check_cycles, root: Path) -> list[list[str]]:
    graph = check_cycles.build_graph(root)
    return check_cycles._sccs(graph)


def test_detects_absolute_import_cycle(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "a", "from agent.b import x\n")
    _write(root, "b", "from agent.c import y\n")
    _write(root, "c", "from agent.a import z\n")
    groups = _cycle_groups(check_cycles, root)
    assert groups == [["agent.a", "agent.b", "agent.c"]]


def test_detects_relative_import_cycle(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "pkg.a", "from . import b\n")
    _write(root, "pkg.b", "from .a import thing\n")
    groups = _cycle_groups(check_cycles, root)
    assert groups == [["agent.pkg.a", "agent.pkg.b"]]


def test_no_cycle_in_acyclic_tree(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "a", "from agent.b import x\n")
    _write(root, "b", "from agent.c import y\n")
    _write(root, "c", "VALUE = 1\n")
    assert _cycle_groups(check_cycles, root) == []


def test_function_level_lazy_import_is_not_a_cycle(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "a", "def f():\n    from agent.b import x\n")
    _write(root, "b", "def g():\n    from agent.a import y\n")
    assert _cycle_groups(check_cycles, root) == []


def test_cross_tree_cycle_is_detected(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "services.x", "from worker.core.y import z\n")
    worker = tmp_path / "worker"
    _write(worker, "core.y", "from agent.services.x import z\n")
    modules = check_cycles.collect_modules([root, worker])
    graph = check_cycles.build_graph(modules=modules)
    groups = check_cycles._sccs(graph)
    assert groups == [["agent.services.x", "worker.core.y"]]


def test_representative_cycle_walks_the_component(tmp_path: Path, check_cycles) -> None:
    root = tmp_path / "agent"
    _write(root, "a", "from agent.b import x\n")
    _write(root, "b", "from agent.c import y\n")
    _write(root, "c", "from agent.a import z\n")
    graph = check_cycles.build_graph(root)
    cycle = check_cycles._representative_cycle(graph, ["agent.a", "agent.b", "agent.c"])
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"agent.a", "agent.b", "agent.c"}


def test_baseline_file_format_is_parseable(check_cycles) -> None:
    baseline = check_cycles._baseline_keys()
    assert baseline, "reports/import-cycle-baseline.txt must exist with at least the tracked legacy cycles"
    for key in baseline:
        members = key.split(",")
        assert members == sorted(members), f"baseline key must be sorted: {key}"
