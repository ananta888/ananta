"""Test tiers: the default ``core`` run and the ``slow`` tier that is not run by default.

A test belongs to the slow tier when

- it lives under ``tests/slow/`` (whole files, moved there by domain: ``tests/slow/<domain>/``),
- it is listed in ``tests/slow_tests.txt`` (files that must stay where they are, because a gate hashes their
  path or content, and single expensive tests inside otherwise fast files), or
- it carries ``@pytest.mark.slow``.

``--tier core`` (default) deselects the slow tier, ``--tier slow`` runs only it, ``--tier all`` runs both;
``ANANTA_TEST_TIER`` sets the default. A slow test still runs in the core tier when its file (or a
directory under ``tests/slow/``) is named explicitly on the command line: gate scripts that run fixed test
files keep their full test set. ``--strict-tier`` drops that exception (for selections that name many
files, such as the affected-tests phase).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SLOW_DIR = TESTS_DIR / "slow"
MANIFEST = TESTS_DIR / "slow_tests.txt"
TIERS = ("core", "slow", "all")
_TIER_ENV = "ANANTA_TEST_TIER"


def add_option(parser: pytest.Parser) -> None:
    parser.addoption(
        "--tier",
        choices=TIERS,
        default=None,
        help=f"core (default, without the slow tier), slow (only it) or all; default from {_TIER_ENV}",
    )
    parser.addoption(
        "--strict-tier",
        action="store_true",
        help="apply the tier also to explicitly named files (default: a named file runs completely)",
    )


def selected_tier(config: pytest.Config) -> str:
    tier = config.getoption("--tier") or os.environ.get(_TIER_ENV, "").strip().lower() or "core"
    if tier not in TIERS:
        raise pytest.UsageError(f"{_TIER_ENV} must be one of {', '.join(TIERS)}, got {tier!r}")
    return tier


def load_manifest(path: Path = MANIFEST) -> tuple[frozenset[str], frozenset[str]]:
    """``(files, tests)`` of the manifest: ``tests/x.py`` names a file, ``tests/x.py::test_y`` one test
    function (all its parametrizations). Blank lines and ``#`` comments are ignored."""
    files: set[str] = set()
    tests: set[str] = set()
    if not path.is_file():
        return frozenset(), frozenset()
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        (tests if "::" in entry else files).add(entry)
    return frozenset(files), frozenset(tests)


def _explicit_paths(config: pytest.Config) -> tuple[Path, ...]:
    if config.getoption("--strict-tier"):
        return ()
    root = Path(str(config.invocation_params.dir))
    paths = []
    for arg in config.invocation_params.args:
        if arg.startswith("-"):
            continue
        candidate = (root / arg.split("::", 1)[0]).resolve()
        if candidate.exists():
            paths.append(candidate)
    return tuple(paths)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _named_explicitly(path: Path, explicit: Iterable[Path]) -> bool:
    """The file itself, or a directory inside ``tests/slow/`` that contains it, was named."""
    for named in explicit:
        if named == path:
            return True
        if named.is_dir() and _is_within(named, SLOW_DIR) and _is_within(path, named):
            return True
    return False


def ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip collecting ``tests/slow/`` in the core tier unless it was named explicitly."""
    if selected_tier(config) != "core":
        return None
    path = Path(collection_path).resolve()
    if not _is_within(path, SLOW_DIR):
        return None
    explicit = _explicit_paths(config)
    if any(_is_within(named, SLOW_DIR) and (_is_within(path, named) or _is_within(named, path)) for named in explicit):
        return None
    return True


def is_slow(item: pytest.Item, manifest: tuple[frozenset[str], frozenset[str]]) -> bool:
    path = Path(str(item.path)).resolve()
    if _is_within(path, SLOW_DIR) or item.get_closest_marker("slow") is not None:
        return True
    files, tests = manifest
    relative = f"tests/{path.relative_to(TESTS_DIR).as_posix()}" if _is_within(path, TESTS_DIR) else ""
    function = getattr(item, "originalname", None) or item.name.split("[", 1)[0]
    return relative in files or f"{relative}::{function}" in tests


def select(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the tests outside the selected tier (in place)."""
    tier = selected_tier(config)
    if tier == "all":
        return
    manifest = load_manifest()
    explicit = _explicit_paths(config)
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        slow = is_slow(item, manifest)
        if tier == "core":
            keep = not slow or _named_explicitly(Path(str(item.path)).resolve(), explicit)
        else:
            keep = slow
        (kept if keep else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
