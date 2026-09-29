#!/usr/bin/env python3
"""Select the test files affected by a change, instead of running the whole suite.

A static import graph of the repository's Python code (``ast``, no imports executed) is walked backwards from
the changed modules to every test file that imports them directly or transitively. Changed files that are
not Python (docs, JSON gate reports, compose files, ...) select the tests whose source names their path.
Changes to the test infrastructure itself (conftest, isolation helpers, dependency pins, the test image)
select the full suite, because every test depends on them.

    python scripts/select_affected_tests.py                 # changes of the working tree vs. HEAD
    python scripts/select_affected_tests.py --base origin/main
    python scripts/select_affected_tests.py --files agent/context_profile.py docs/x.md
    python scripts/select_affected_tests.py --explain       # why each test file was selected

Prints one test path per line, or the single line ``tests`` when the full suite is needed.
The selection is a fast pre-merge signal; the full suite stays the release gate.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import warnings
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = "tests"
FULL_SUITE = "tests"

# directories that hold no importable project code (or are huge and irrelevant)
_SKIPPED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "frontend-angular",
        "project-workspaces",
        "data",
        "data_test",
        "artifacts",
        "ci-artifacts",
        "test-results",
        "test-reports",
        "rag-helper",
        "autoimport-state",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
    }
)

# a change here affects every test
_FULL_SUITE_TRIGGERS = (
    "tests/conftest.py",
    "tests/isolation_database.py",
    "tests/isolation_guard.py",
    "tests/sqlite_schema_template.py",
    "tests/password_hash_cache.py",
    "tests/werkzeug_rule_cache.py",
    "tests_support.py",
    "pyproject.toml",
    "requirements.lock",
    "requirements-dev.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "docker/compose-next/Dockerfile.tests-backend",
    "docker/compose-next/compose.tests-backend-base.yml",
)


@dataclass
class Selection:
    full_suite: bool = False
    tests: dict[str, list[str]] = field(default_factory=dict)  # test file -> reasons

    def add(self, test: str, reason: str) -> None:
        self.tests.setdefault(test, []).append(reason)


def _python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in _SKIPPED_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        yield path


def module_name(relative: str) -> str:
    """``agent/services/x.py`` -> ``agent.services.x``; a package's ``__init__.py`` is the package."""
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_package(relative: str) -> bool:
    return Path(relative).name == "__init__.py"


def imported_modules(source: str, *, relative: str) -> set[str]:
    """Absolute module names imported by a file (relative imports resolved; ``from a import b`` yields
    ``a`` and ``a.b``, since ``b`` may be a submodule)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # e.g. invalid escape sequences in unrelated scripts
            tree = ast.parse(source, filename=relative)
    except (SyntaxError, ValueError):
        return set()
    own = module_name(relative)
    package = own if _is_package(relative) else own.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".") if package else []
                if node.level > 1:
                    anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, node.module] if node.module else anchor)
            else:
                base = node.module or ""
            if not base:
                continue
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


class ImportGraph:
    """Which project files import which: reverse edges from a module to the files that import it."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._files = dict(sources)
        self._module_to_file = {module_name(path): path for path in self._files}
        self._importers: dict[str, set[str]] = defaultdict(set)
        for path, source in self._files.items():
            for module in imported_modules(source, relative=path):
                target = self._resolve(module)
                if target and target != path:
                    self._importers[target].add(path)

    @classmethod
    def from_repository(cls, root: Path = ROOT) -> ImportGraph:
        sources = {}
        for path in _python_files(root):
            try:
                sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return cls(sources)

    def _resolve(self, module: str) -> str | None:
        # the longest known prefix: `agent.services.x.Symbol` -> agent/services/x.py
        parts = module.split(".")
        while parts:
            path = self._module_to_file.get(".".join(parts))
            if path:
                return path
            parts.pop()
        return None

    @property
    def files(self) -> Mapping[str, str]:
        return self._files

    def dependents(self, changed: str, *, depth: int | None = None) -> dict[str, str]:
        """Every file that imports ``changed`` directly or transitively (up to ``depth`` import hops;
        ``None``: unbounded) -> the file it was reached from."""
        reached: dict[str, str] = {}
        queue = deque([(changed, 0)])
        while queue:
            current, hops = queue.popleft()
            if depth is not None and hops >= depth:
                continue
            for importer in self._importers.get(current, ()):
                if importer not in reached and importer != changed:
                    reached[importer] = current
                    queue.append((importer, hops + 1))
        return reached


def is_test_file(path: str) -> bool:
    name = Path(path).name
    return path.startswith(f"{TESTS_DIR}/") and name.startswith("test_") and name.endswith(".py")


def select(changed_paths: Sequence[str], graph: ImportGraph, *, depth: int | None = None) -> Selection:
    selection = Selection()
    for raw in changed_paths:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        if path in _FULL_SUITE_TRIGGERS or (path.startswith(f"{TESTS_DIR}/") and Path(path).name == "conftest.py"):
            selection.full_suite = True
            selection.add(FULL_SUITE, f"test infrastructure changed: {path}")
            continue
        if is_test_file(path):
            if (ROOT / path).exists() or path in graph.files:
                selection.add(path, "changed test file")
        if path.endswith(".py") and path in graph.files:
            for dependent, via in graph.dependents(path, depth=depth).items():
                if is_test_file(dependent):
                    selection.add(dependent, f"imports {path}" + (f" (via {via})" if via != path else ""))
            continue
        if not path.endswith(".py"):
            # tests that name the path, and tests reaching a module that names it (e.g. a gate script
            # whose source projection lists the file)
            for referencing, source in graph.files.items():
                if path not in source:
                    continue
                if is_test_file(referencing):
                    selection.add(referencing, f"references {path}")
                for dependent, via in graph.dependents(referencing, depth=depth).items():
                    if is_test_file(dependent):
                        selection.add(dependent, f"imports {referencing}, which references {path}")
    return selection


def changed_files(base: str | None, root: Path = ROOT) -> list[str]:
    """Changed, staged and untracked files vs. ``base`` (default: HEAD, i.e. the working tree's changes)."""
    commands = [
        ["git", "diff", "--name-only", base or "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    if base:
        commands.append(["git", "diff", "--name-only", f"{base}...HEAD"])
    names: set[str] = set()
    for command in commands:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise SystemExit(f"git failed: {' '.join(command)}: {completed.stderr.strip()}")
        names.update(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return sorted(names)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--base", help="git revision to compare with (e.g. origin/main); default: HEAD")
    source.add_argument("--files", nargs="+", help="explicit changed paths instead of git")
    parser.add_argument("--explain", action="store_true", help="print why each test file was selected")
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="follow at most N import hops (default: transitive, the safe choice; 1-2 = fast, narrower feedback)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    changed = arguments.files if arguments.files else changed_files(arguments.base)
    selection = select(changed, ImportGraph.from_repository(), depth=arguments.depth)
    shown = {FULL_SUITE: selection.tests.get(FULL_SUITE, [])} if selection.full_suite else selection.tests
    for test in sorted(shown):
        reasons = sorted(set(shown[test]))
        print(f"{test}  # {reasons[0]}" if arguments.explain and reasons else test)
    if not selection.tests:
        print("no affected tests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
