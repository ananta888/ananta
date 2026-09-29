from __future__ import annotations

from pathlib import Path

from scripts.move_tests_to_slow import anchor_levels, rewrite
from scripts.update_test_durations import file_of_classname
from tests.sharding import assign
from tests.tiering import load_manifest


def test_manifest_names_files_and_single_test_functions(tmp_path: Path) -> None:
    manifest = tmp_path / "slow_tests.txt"
    manifest.write_text(
        "# comment\n\ntests/test_a.py  # gate-hashed\ntests/meet/test_b.py::test_expensive\n", encoding="utf-8"
    )
    assert load_manifest(manifest) == (
        frozenset({"tests/test_a.py"}),
        frozenset({"tests/meet/test_b.py::test_expensive"}),
    )
    assert load_manifest(tmp_path / "missing.txt") == (frozenset(), frozenset())


def test_shards_are_deterministic_and_balanced_longest_first() -> None:
    weights = {"a.py": 10.0, "b.py": 6.0, "c.py": 5.0, "d.py": 4.0, "e.py": 1.0}
    first, second = assign(weights, 2), assign(dict(reversed(list(weights.items()))), 2)
    assert first == second
    loads = [sum(weight for name, weight in weights.items() if first[name] == shard) for shard in (0, 1)]
    # longest first to the lightest shard: 10 | 6, 5 -> 6+5 | 4 -> 10+4 | 1 -> 11+1
    assert sorted(loads) == [12.0, 14.0]
    assert all(sum(1 for shard in first.values() if shard == index) for index in (0, 1))


def test_anchor_levels_count_ancestors_of_the_test_file() -> None:
    source = (
        "A = Path(__file__).parent\n"
        "B = Path(__file__).resolve().parent.parent\n"
        "C = Path(__file__).parents[2]\n"
        "D = os.path.dirname(__file__)\n"
    )
    assert sorted(anchor_levels(source)) == [0, 0, 1, 2]


def test_references_to_moved_files_follow_them_into_the_slow_tier() -> None:
    moves = {"tests/meet/test_x.py": "tests/slow/meet/test_x.py", "tests/test_y.py": "tests/slow/test_y.py"}
    source = (
        "from tests.meet.test_x import helper\n"
        "run('tests/test_y.py::test_z')\n"
        "keep = 'tests/meet/test_x_other.py', tests.meet.test_x_other\n"
    )
    assert rewrite(source, moves) == (
        "from tests.slow.meet.test_x import helper\n"
        "run('tests/slow/test_y.py::test_z')\n"
        "keep = 'tests/meet/test_x_other.py', tests.meet.test_x_other\n"
    )


def test_junit_classnames_map_to_test_files() -> None:
    assert file_of_classname("tests.meet.test_x.TestY") == "tests/meet/test_x.py"
    assert file_of_classname("tests.test_y") == "tests/test_y.py"
    assert file_of_classname("tests.helpers") is None
