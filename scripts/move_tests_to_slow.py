#!/usr/bin/env python3
"""Move test files into the slow tier: ``tests/<path>`` -> ``tests/slow/<path>``, or list them in
``tests/slow_tests.txt`` when they must stay where they are.

    python scripts/move_tests_to_slow.py tests/meet/test_x.py tests/test_y.py            # dry run
    python scripts/move_tests_to_slow.py --apply tests/meet/test_x.py tests/test_y.py
    python scripts/move_tests_to_slow.py --apply --from-file .tmp/slow_candidates.txt

A file stays in place (and goes into the manifest) when

- a gate hashes it: ``artifacts/``, ``scripts/`` or a gate-hashed doc/todo names its path or module
  (moving or rewriting it would stale gate evidence; see scripts/migrate_test_domain.py),
- its directory has its own conftest (its fixtures would not reach ``tests/slow/``),
- it anchors on a directory inside ``tests/`` through ``__file__`` (the anchor would point elsewhere),
- it opens a sibling by name (``with_name``) or imports relatively (``from .x import``).

Moved files keep their relative path below ``tests/slow/``; anchors on ``tests/`` or above get one more
level, and every reference (module ``tests.<a>.<b>``, path ``tests/<a>/<b>.py``) outside ``artifacts/``
and gate-hashed sources is rewritten.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_test_domain import (  # noqa: E402
    _pinning_text,
    _text_files,
    deepen_file_anchors,
    hashed_sources,
)

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SLOW = TESTS / "slow"
MANIFEST = TESTS / "slow_tests.txt"

_PARENT_CHAIN = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?((?:\.parent(?!s))+)")
_PARENTS_INDEX = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?\.parents\[(\d+)\]")
_DIRNAME = re.compile(r"os\.path\.dirname\(__file__\)")


def anchor_levels(source: str) -> list[int]:
    """0-based ancestor index of every ``__file__`` anchor (``.parent`` = 0, ``.parents[2]`` = 2)."""
    levels = [match.group(1).count(".parent") - 1 for match in _PARENT_CHAIN.finditer(source)]
    levels += [int(match.group(1)) for match in _PARENTS_INDEX.finditer(source)]
    levels += [0 for _ in _DIRNAME.finditer(source)]
    return levels


def stay_reason(path: Path, pinning: str) -> str | None:
    relative = path.relative_to(ROOT).as_posix()
    module = relative[: -len(".py")].replace("/", ".")
    if relative in pinning or re.search(rf"(?<![A-Za-z0-9_.]){re.escape(module)}\b", pinning):
        return "gate-hashed"
    if any((parent / "conftest.py").is_file() for parent in path.parents if parent != TESTS and TESTS in parent.parents):
        return "directory conftest"
    source = path.read_text(encoding="utf-8", errors="ignore")
    depth = len(path.relative_to(TESTS).parts) - 1  # directories between tests/ and the file
    if any(level < depth for level in anchor_levels(source)):
        return "anchors inside tests/"
    if "with_name(" in source:
        return "opens a sibling by name"
    if re.search(r"(?m)^\s*from \.", source):
        return "relative import"
    return None


def rewrite(source: str, moves: dict[str, str]) -> str:
    """``tests/<a>.py`` -> ``tests/slow/<a>.py`` and ``tests.<a>`` -> ``tests.slow.<a>`` for moved files."""
    if not moves:
        return source
    paths = sorted(moves, key=len, reverse=True)
    path_pattern = re.compile(r"(?<![A-Za-z0-9_/.-])(" + "|".join(map(re.escape, paths)) + r")(?![A-Za-z0-9_.-])")
    source = path_pattern.sub(lambda match: moves[match.group(1)], source)
    modules = {old[:-3].replace("/", "."): new[:-3].replace("/", ".") for old, new in moves.items()}
    module_pattern = re.compile(
        r"(?<![A-Za-z0-9_.])(" + "|".join(map(re.escape, sorted(modules, key=len, reverse=True))) + r")\b"
    )
    return module_pattern.sub(lambda match: modules[match.group(1)], source)


def _ensure_packages(directory: Path) -> list[Path]:
    created = []
    for package in [directory, *directory.parents]:
        if package == TESTS:
            break
        init = package / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
            created.append(init)
    return created


def _append_manifest(entries: list[tuple[str, str]]) -> None:
    existing = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
    known = {line.split("#", 1)[0].strip() for line in existing.splitlines()}
    lines = [f"{relative}  # {reason}" for relative, reason in entries if relative not in known]
    if lines:
        MANIFEST.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


def move(paths: list[Path], *, apply: bool) -> int:
    pinning_text = _pinning_text()
    hashed = hashed_sources(pinning_text)
    pinning = pinning_text + "".join(path.read_text(encoding="utf-8", errors="ignore") for path in hashed)
    moving: list[Path] = []
    staying: list[tuple[str, str]] = []
    for path in paths:
        if SLOW in path.parents:
            continue
        reason = stay_reason(path, pinning)
        if reason:
            staying.append((path.relative_to(ROOT).as_posix(), reason))
        else:
            moving.append(path)
    print(f"{len(moving)} files move to tests/slow/, {len(staying)} go into tests/slow_tests.txt")
    for relative, reason in staying:
        print(f"  stays: {relative} ({reason})")
    if not apply:
        return 0
    moves = {path.relative_to(ROOT).as_posix(): (SLOW / path.relative_to(TESTS)).relative_to(ROOT).as_posix() for path in moving}
    created: list[Path] = []
    for path in moving:
        target = SLOW / path.relative_to(TESTS)
        target.parent.mkdir(parents=True, exist_ok=True)
        created += _ensure_packages(target.parent)
        subprocess.run(["git", "mv", str(path), str(target)], cwd=ROOT, check=True)
        target.write_text(deepen_file_anchors(target.read_text(encoding="utf-8")), encoding="utf-8")
    skip = {str(path) for path in hashed}
    rewritten = 0
    for path in _text_files([ROOT]):
        if str(path) in skip:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated = rewrite(source, moves)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            rewritten += 1
    _append_manifest(staying)
    if created:
        subprocess.run(["git", "add", *map(str, created)], cwd=ROOT, check=True)
    print(f"moved {len(moving)} files, rewrote references in {rewritten} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--from-file", type=Path, help="one test file path per line")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)
    paths = list(arguments.paths)
    if arguments.from_file:
        paths += [Path(line.strip()) for line in arguments.from_file.read_text().splitlines() if line.strip()]
    resolved = [(ROOT / path).resolve() if not path.is_absolute() else path for path in paths]
    missing = [path for path in resolved if not path.is_file()]
    if missing:
        raise SystemExit(f"not a file: {missing[0]}")
    return move(resolved, apply=arguments.apply)


if __name__ == "__main__":
    raise SystemExit(main())
