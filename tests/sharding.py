"""Split the selected tests into balanced shards for parallel CI jobs.

``--shard-count N --shard-index I`` (0-based) keeps the tests of every file assigned to shard ``I``. Files
are assigned greedily, longest first, to the currently lightest shard, using the measured per-file
runtimes in ``tests/test_durations.json`` (``scripts/update_test_durations.py`` writes it from a JUnit
report); files without a measurement are estimated from their test count. Whole files stay together, so
module fixtures are set up once. The assignment is deterministic: every job computes the same split.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DURATIONS = TESTS_DIR / "test_durations.json"
_UNMEASURED_SECONDS_PER_TEST = 0.1


def add_options(parser: pytest.Parser) -> None:
    parser.addoption("--shard-count", type=int, default=1, help="split the selected tests into N shards")
    parser.addoption("--shard-index", type=int, default=0, help="0-based shard to run (with --shard-count)")


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(name): float(seconds) for name, seconds in dict(payload.get("files", {})).items()}


def assign(weights: dict[str, float], count: int) -> dict[str, int]:
    """File -> shard: longest processing time first, ties broken by name."""
    loads = [0.0] * count
    shard_of: dict[str, int] = {}
    for name, weight in sorted(weights.items(), key=lambda entry: (-entry[1], entry[0])):
        target = min(range(count), key=lambda index: (loads[index], index))
        shard_of[name] = target
        loads[target] += weight
    return shard_of


def _file_key(item: pytest.Item, root: Path) -> str:
    path = Path(str(item.path)).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def select(config: pytest.Config, items: list[pytest.Item]) -> None:
    count = int(config.getoption("--shard-count"))
    index = int(config.getoption("--shard-index"))
    if count <= 1:
        return
    if not 0 <= index < count:
        raise pytest.UsageError(f"--shard-index must be in [0, {count - 1}], got {index}")
    root = TESTS_DIR.parent
    measured = load_durations()
    tests_per_file: dict[str, int] = defaultdict(int)
    for item in items:
        tests_per_file[_file_key(item, root)] += 1
    weights = {
        name: measured.get(name, tests * _UNMEASURED_SECONDS_PER_TEST) for name, tests in tests_per_file.items()
    }
    shard_of = assign(weights, count)
    kept = [item for item in items if shard_of[_file_key(item, root)] == index]
    deselected = [item for item in items if shard_of[_file_key(item, root)] != index]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
