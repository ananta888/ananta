from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path


def effective_extension(path: Path) -> str:
    """Return the dispatch key for suffix and exact-name source files."""

    name = path.name.lower()
    if (
        name in {"dockerfile", "containerfile"}
        or name.startswith(("dockerfile.", "containerfile."))
        or name.endswith((".dockerfile", ".containerfile"))
    ):
        return "dockerfile"
    if name in {"makefile", "gnumakefile"}:
        return "makefile"
    if name == "jenkinsfile" or name.startswith("jenkinsfile."):
        return "jenkinsfile"
    extension = path.suffix.lower().lstrip(".")
    if extension:
        return extension
    return _extensionless_script_dispatch(path)


def _extensionless_script_dispatch(path: Path) -> str:
    """Classify an extensionless script from one inert, bounded first line."""

    try:
        if path.is_symlink() or not path.is_file():
            return ""
        with path.open("rb") as handle:
            first_line = handle.readline(512).decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""
    if not first_line.startswith("#!"):
        return ""
    if re.search(r"(?:^|[/\s])python(?:[0-9.]+)?(?:\s|$)", first_line):
        return "py"
    if re.search(r"(?:^|[/\s])pwsh(?:\s|$)", first_line):
        return "ps1"
    if re.search(r"(?:^|[/\s])fish(?:\s|$)", first_line):
        return "fish"
    if re.search(r"(?:^|[/\s])(?:ba|da|k|z)?sh(?:\s|$)", first_line):
        return "sh"
    return ""


def should_include_file(
    path: Path,
    root: Path,
    extensions: set[str],
    excluded_parts: set[str],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> bool:
    try:
        if path.is_symlink():
            return False
    except OSError:
        return False
    if any(part in excluded_parts for part in path.parts):
        return False

    ext = effective_extension(path)
    if ext not in extensions:
        return False

    rel_path = path.relative_to(root).as_posix()
    candidates = _match_candidates(rel_path)

    if include_globs and not any(_matches_any_glob(candidates, pattern) for pattern in include_globs):
        return False
    if exclude_globs and any(_matches_any_glob(candidates, pattern) for pattern in exclude_globs):
        return False
    return True


def exclude_gitignored_files(root: Path, files: list[Path]) -> list[Path]:
    if not files:
        return files

    rel_paths = [path.relative_to(root).as_posix() for path in files]
    ignored_rel_paths = _git_check_ignore(root, rel_paths)
    if not ignored_rel_paths:
        ignored_rel_paths = _fallback_gitignore_matches(root, rel_paths)
    if not ignored_rel_paths:
        return files
    return [path for path in files if path.relative_to(root).as_posix() not in ignored_rel_paths]


def _git_check_ignore(root: Path, rel_paths: list[str]) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin"],
            input="\n".join(rel_paths),
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode not in {0, 1}:
        return set()
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def _matches_any_glob(candidates: list[str], pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    return any(fnmatch(candidate, normalized_pattern) for candidate in candidates)


def _match_candidates(rel_path: str) -> list[str]:
    normalized = rel_path.replace("\\", "/")
    parts = normalized.split("/")
    candidates = {normalized, parts[-1]}
    for index in range(len(parts)):
        candidates.add("/".join(parts[index:]))
    return sorted(candidates)


def _fallback_gitignore_matches(root: Path, rel_paths: list[str]) -> set[str]:
    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        return set()

    patterns: list[tuple[str, bool]] = []
    for raw_line in gitignore_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        is_negated = line.startswith("!")
        if is_negated:
            line = line[1:]
        patterns.append((line.replace("\\", "/"), is_negated))

    ignored: set[str] = set()
    for rel_path in rel_paths:
        candidates = _match_candidates(rel_path)
        decision = False
        for pattern, is_negated in patterns:
            normalized_pattern = pattern
            if normalized_pattern.endswith("/"):
                normalized_pattern = normalized_pattern.rstrip("/") + "/**"
            if any(fnmatch(candidate, normalized_pattern) for candidate in candidates):
                decision = not is_negated
        if decision:
            ignored.add(rel_path)
    return ignored
