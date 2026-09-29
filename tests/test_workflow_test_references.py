"""Every Python test path a GitHub workflow names must exist, and every glob must match.

Moving tests into domain folders or the slow tier silently shrank workflows before: a flat glob such as
``tests/test_ml_intern_*.py`` under ``shopt -s nullglob`` kept the Unsloth gate green with 23 files fewer,
and a glob that matched nothing any more broke the workflow-transition track.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
_TEST_REFERENCE = re.compile(r"(?<![\w/.-])(tests/[\w./*\-]+\.py)")


def _references(workflow: Path) -> list[str]:
    return sorted(set(_TEST_REFERENCE.findall(workflow.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_test_references_resolve(workflow: Path) -> None:
    unresolved = [
        reference
        for reference in _references(workflow)
        if not (glob.glob(str(ROOT / reference)) if "*" in reference else (ROOT / reference).is_file())
    ]
    assert not unresolved, f"{workflow.name} names tests that do not exist (moved?): {unresolved}"
