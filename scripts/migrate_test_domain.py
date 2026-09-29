#!/usr/bin/env python3
"""Move one domain's flat test files into ``tests/<domain>/`` and rewrite every reference to them.

    python scripts/migrate_test_domain.py meet            # dry run: what moves, what stays pinned
    python scripts/migrate_test_domain.py meet --apply

Selected: ``tests/test_<prefix>_*.py``, ``tests/test_<prefix>.py`` and the domain's helper files
``tests/<prefix>_*`` (``.py``, ``.mjs``, ...). File names stay unchanged.

Pinned (left in place): files named by ``artifacts/`` or ``scripts/``, or by a gate-hashed source (a doc or
todo that ``artifacts/`` or ``scripts/`` name). Their paths or contents are part of hashed gate source
projections, so moving them, or editing the sources that name them, would stale gate evidence. The closure
of pinned files stays too: helpers they import (``tests.<module>``) or open as siblings (``with_name``).

Rewritten:
- in moved files, ``__file__``-relative anchors (``.parent``, ``.parents[N]``, ``os.path.dirname``) get
  one more level, so they keep pointing at the same directories;
- everywhere except ``artifacts/`` and gate-hashed sources, module names ``tests.<stem>`` and paths
  ``tests/<name>``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PINNING_DIRS = ("artifacts", "scripts")
# never rewritten: hashed gate evidence, dependencies, generated output
_EXCLUDED_DIRS = frozenset(
    {".git", "node_modules", "artifacts", "project-workspaces", "data", "data_test", "rag-helper", "ci-artifacts",
     "test-results", "test-reports", "autoimport-state", "vendor", "__pycache__", ".venv", "venv"}
)
_TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh", ".txt", ".mjs", ".js", ".ts", ".cfg", ".ini", ".ps1"}
)


def domain_files(prefix: str) -> list[Path]:
    found = set(TESTS.glob(f"test_{prefix}_*.py")) | set(TESTS.glob(f"test_{prefix}.py"))
    found |= {path for path in TESTS.glob(f"{prefix}_*") if path.is_file()}
    return sorted(found)


def _text_files(roots: list[Path]) -> list[Path]:
    files = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            if any(part in _EXCLUDED_DIRS for part in path.relative_to(ROOT).parts[:-1]):
                continue
            files.append(path)
    return files


def _pinning_text() -> str:
    text = ""
    for directory in PINNING_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix in _TEXT_SUFFIXES:
                try:
                    text += path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
    return text


_REPO_PATH = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+)")


def hashed_sources(pinning_text: str) -> set[Path]:
    """Repository files outside ``tests/``, ``artifacts/`` and ``scripts/`` that the pinning dirs name, e.g.
    docs and todos in a gate's source projection: their bytes are hashed, so they must not be rewritten."""
    found = set()
    for relative in set(_REPO_PATH.findall(pinning_text)):
        if relative.split("/", 1)[0] in {"tests", *PINNING_DIRS}:
            continue
        path = ROOT / relative
        if path.suffix in _TEXT_SUFFIXES and path.is_file():
            found.add(path)
    return found


def pinned_files(candidates: list[Path], hashed: set[Path] | None = None) -> set[Path]:
    references = _pinning_text()
    for path in hashed if hashed is not None else hashed_sources(references):
        try:
            references += path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    pinned = {path for path in candidates if f"tests/{path.name}" in references}
    by_stem = {path.stem: path for path in candidates}
    by_name = {path.name: path for path in candidates}
    changed = True
    while changed:  # the closure: what a pinned file imports or opens next to itself stays with it
        changed = False
        for path in list(pinned):
            if path.suffix != ".py":
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            for stem in re.findall(r"\btests\.([A-Za-z0-9_]+)", source):
                helper = by_stem.get(stem)
                if helper and helper not in pinned:
                    pinned.add(helper)
                    changed = True
            named = re.findall(r"with_name\(\s*[\"']([^\"']+)[\"']", source)
            named += re.findall(r"(?<![A-Za-z0-9_/.-])tests/([A-Za-z0-9_.-]+)", source)  # path strings
            for name in named:
                sibling = by_name.get(name)
                if sibling and sibling not in pinned:
                    pinned.add(sibling)
                    changed = True
    # a movable file that opens a pinned sibling by name has to stay next to it
    for path in candidates:
        if path in pinned or path.suffix != ".py":
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if any(by_name.get(name) in pinned for name in re.findall(r"with_name\(\s*[\"']([^\"']+)[\"']", source)):
            pinned.add(path)
    return pinned


_ANCHORS = (
    (re.compile(r"(Path\(__file__\)(?:\.resolve\(\))?\.parents\[)(\d+)(\])"), lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}"),
    (re.compile(r"(Path\(__file__\)(?:\.resolve\(\))?\.parent)(?!s)"), lambda m: f"{m.group(1)}.parent"),
    (re.compile(r"os\.path\.dirname\(__file__\)"), lambda m: "os.path.dirname(os.path.dirname(__file__))"),
)


_JS_RELATIVE = re.compile(r"""((?:\bfrom|\bimport|require\()\s*\(?\s*['"])\.\./""")


def deepen_js_relative_imports(source: str) -> str:
    """Relative ``../`` module specifiers of a moved ``.mjs``/``.js`` file, one level deeper."""
    return _JS_RELATIVE.sub(lambda m: f"{m.group(1)}../../", source)


def deepen_file_anchors(source: str) -> str:
    """``__file__``-relative directories as seen from one level deeper."""
    for pattern, replacement in _ANCHORS:
        source = pattern.sub(replacement, source)
    return source


def rewrite_references(source: str, moves: dict[str, str]) -> str:
    """``tests.<stem>`` -> ``tests.<dir>.<stem>`` and ``tests/<name>`` -> ``tests/<dir>/<name>``."""
    if not moves:
        return source
    names = sorted(moves, key=len, reverse=True)
    path_pattern = re.compile(r"(?<![A-Za-z0-9_/.-])tests/(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_.-])")
    source = path_pattern.sub(lambda m: f"tests/{moves[m.group(1)]}/{m.group(1)}", source)
    stems = {Path(name).stem: directory for name, directory in moves.items() if name.endswith(".py")}
    if stems:
        # `from tests import a, b` -> one import line per target package
        def _split_from_tests(match: re.Match[str]) -> str:
            indent, names = match.group(1), [item.strip() for item in match.group(2).split(",") if item.strip()]
            groups: dict[str, list[str]] = {}
            for item in names:
                module = item.split(" as ")[0].strip()
                groups.setdefault(stems.get(module, ""), []).append(item)
            return "\n".join(
                f"{indent}from tests{'.' + target if target else ''} import {', '.join(items)}"
                for target, items in groups.items()
            )

        source = re.sub(r"(?m)^([ \t]*)from tests import ([A-Za-z0-9_, ]+(?: as [A-Za-z0-9_]+)?)$", _split_from_tests, source)
        module_pattern = re.compile(r"(?<![A-Za-z0-9_.])tests\.(" + "|".join(map(re.escape, sorted(stems, key=len, reverse=True))) + r")\b")
        source = module_pattern.sub(lambda m: f"tests.{stems[m.group(1)]}.{m.group(1)}", source)
    return source


def migrate(prefix: str, directory: str, *, apply: bool) -> int:
    candidates = domain_files(prefix)
    hashed = hashed_sources(_pinning_text())
    pinned = pinned_files(candidates, hashed)
    moving = [path for path in candidates if path not in pinned]
    print(f"{prefix}: {len(candidates)} files, {len(moving)} move to tests/{directory}/, {len(pinned)} pinned")
    for path in sorted(pinned):
        print(f"  pinned: tests/{path.name}")
    if not apply or not moving:
        return 0
    target = TESTS / directory
    target.mkdir(exist_ok=True)
    init = target / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    moves = {path.name: directory for path in moving}
    for path in moving:
        subprocess.run(["git", "mv", str(path), str(target / path.name)], cwd=ROOT, check=True)
    for path in moving:
        moved = target / path.name
        if moved.suffix == ".py":
            moved.write_text(deepen_file_anchors(moved.read_text(encoding="utf-8")), encoding="utf-8")
        elif moved.suffix in {".mjs", ".js"}:
            moved.write_text(deepen_js_relative_imports(moved.read_text(encoding="utf-8")), encoding="utf-8")
    pinned_now = {str(path) for path in pinned | hashed}
    rewritten = 0
    for path in _text_files([ROOT]):
        if str(path) in pinned_now:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated = rewrite_references(source, moves)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            rewritten += 1
    subprocess.run(["git", "add", str(init)], cwd=ROOT, check=True)
    print(f"moved {len(moving)} files, rewrote references in {rewritten} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefix", help="file prefix of the domain, e.g. meet")
    parser.add_argument("--dir", help="target directory under tests/ (default: the prefix)")
    parser.add_argument("--apply", action="store_true", help="move and rewrite (default: dry run)")
    arguments = parser.parse_args(argv)
    return migrate(arguments.prefix, arguments.dir or arguments.prefix, apply=arguments.apply)


if __name__ == "__main__":
    sys.exit(main())
