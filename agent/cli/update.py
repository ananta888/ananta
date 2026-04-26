from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stderr: str) -> None:
        self.command = list(command)
        self.returncode = int(returncode)
        self.stderr = str(stderr or "").strip()
        super().__init__(self._message())

    def _message(self) -> str:
        rendered = " ".join(self.command)
        if self.stderr:
            return f"command failed ({self.returncode}): {rendered}\n{self.stderr}"
        return f"command failed ({self.returncode}): {rendered}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta update",
        description="Update an existing Ananta checkout safely and refresh dependencies.",
    )
    parser.add_argument("--repo-dir", help="Path to the Ananta git checkout (auto-detected by default).")
    parser.add_argument("--ref", help="Optional branch/tag/ref to checkout before update.")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow update/rollback when git working tree has uncommitted changes.",
    )
    parser.add_argument("--venv", help="Path to virtualenv directory to use for dependency refresh.")
    parser.add_argument("--python", dest="python_executable", help="Explicit Python executable for pip/smoke commands.")
    parser.add_argument("--skip-deps", action="store_true", help="Skip dependency refresh (`pip install -e .`).")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip post-update smoke check.")
    parser.add_argument(
        "--rollback-to",
        help="Rollback checkout to a previous commit SHA/tag/ref instead of pulling latest changes.",
    )
    return parser


def _run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=True,
    )


def _run_checked(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = _run(command, cwd=cwd)
    if result.returncode != 0:
        raise CommandError(command=command, returncode=result.returncode, stderr=result.stderr)
    return result


def _is_ananta_repo(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return False
    text = pyproject.read_text(encoding="utf-8", errors="ignore")
    return 'name = "ananta"' in text


def _discover_repo_dir() -> Path | None:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if _is_ananta_repo(candidate):
            return candidate

    env_root = str(os.getenv("ANANTA_INSTALL_DIR") or "").strip()
    if env_root:
        env_path = Path(env_root).expanduser().resolve()
        if _is_ananta_repo(env_path):
            return env_path

    package_root = Path(__file__).resolve().parents[2]
    if _is_ananta_repo(package_root):
        return package_root
    return None


def _resolve_repo_dir(repo_dir_arg: str | None) -> Path:
    if repo_dir_arg:
        candidate = Path(repo_dir_arg).expanduser().resolve()
        if not _is_ananta_repo(candidate):
            raise ValueError(f"Invalid Ananta repository: {candidate}")
        return candidate
    discovered = _discover_repo_dir()
    if discovered is None:
        raise ValueError("Could not detect Ananta repository. Use --repo-dir <path>.")
    return discovered


def _git_stdout(repo_dir: Path, *args: str) -> str:
    result = _run_checked(["git", *args], cwd=repo_dir)
    return str(result.stdout or "").strip()


def _verify_clean_or_allowed(repo_dir: Path, *, allow_dirty: bool) -> None:
    dirty = _git_stdout(repo_dir, "status", "--porcelain")
    if dirty and not allow_dirty:
        raise ValueError("Working tree is dirty. Commit/stash changes or rerun with --allow-dirty.")


def _python_from_venv(venv_dir: Path) -> Path:
    candidates = [venv_dir / "bin" / "python", venv_dir / "Scripts" / "python.exe"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ValueError(f"No Python executable found in virtualenv: {venv_dir}")


def _resolve_python_executable(args: argparse.Namespace, repo_dir: Path) -> Path:
    explicit_python = str(args.python_executable or "").strip()
    if explicit_python:
        candidate = Path(explicit_python).expanduser().resolve()
        if not candidate.exists():
            raise ValueError(f"Configured Python executable does not exist: {candidate}")
        return candidate

    explicit_venv = str(args.venv or "").strip()
    if explicit_venv:
        return _python_from_venv(Path(explicit_venv).expanduser().resolve())

    active_venv = str(os.getenv("VIRTUAL_ENV") or "").strip()
    if active_venv:
        return _python_from_venv(Path(active_venv).expanduser().resolve())

    local_venv = repo_dir / ".venv"
    if local_venv.exists():
        return _python_from_venv(local_venv)

    return Path(sys.executable).resolve()


def _refresh_dependencies(repo_dir: Path, python_executable: Path) -> None:
    _run_checked([str(python_executable), "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_dir)
    _run_checked([str(python_executable), "-m", "pip", "install", "-e", "."], cwd=repo_dir)


def _run_smoke_check(repo_dir: Path, python_executable: Path) -> None:
    _run_checked([str(python_executable), "-m", "agent.cli.main", "--help"], cwd=repo_dir)


def _checkout_ref(repo_dir: Path, ref: str) -> None:
    _run_checked(["git", "checkout", ref], cwd=repo_dir)


def _pull_latest(repo_dir: Path) -> None:
    branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise ValueError("Repository is in detached HEAD state. Use --ref <branch-or-tag> to update explicitly.")
    _run_checked(["git", "pull", "--ff-only", "origin", branch], cwd=repo_dir)


def _print_update_summary(*, repo_dir: Path, previous_commit: str, current_commit: str) -> None:
    print(f"Repository: {repo_dir}")
    print(f"Previous commit: {previous_commit}")
    print(f"Current commit:  {current_commit}")
    if previous_commit != current_commit:
        print("Update result: updated to a new commit.")
    else:
        print("Update result: already up to date.")
    print(
        "Rollback command: "
        f"ananta update --repo-dir \"{repo_dir}\" --rollback-to {previous_commit}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        repo_dir = _resolve_repo_dir(args.repo_dir)
        _verify_clean_or_allowed(repo_dir, allow_dirty=bool(args.allow_dirty))
        previous_commit = _git_stdout(repo_dir, "rev-parse", "HEAD")
        print(f"Detected Ananta repository: {repo_dir}")
        print(f"Current branch: {_git_stdout(repo_dir, 'rev-parse', '--abbrev-ref', 'HEAD')}")

        if args.rollback_to:
            _checkout_ref(repo_dir, str(args.rollback_to).strip())
        else:
            _run_checked(["git", "fetch", "--tags", "--prune", "origin"], cwd=repo_dir)
            if args.ref:
                _checkout_ref(repo_dir, str(args.ref).strip())
                branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
                if branch != "HEAD":
                    _run_checked(["git", "pull", "--ff-only", "origin", branch], cwd=repo_dir)
                else:
                    print("Checked out detached ref; skipping pull step.")
            else:
                _pull_latest(repo_dir)

        python_executable = _resolve_python_executable(args, repo_dir)
        print(f"Using Python: {python_executable}")
        if not args.skip_deps:
            _refresh_dependencies(repo_dir, python_executable)
        if not args.skip_smoke:
            _run_smoke_check(repo_dir, python_executable)

        current_commit = _git_stdout(repo_dir, "rev-parse", "HEAD")
        _print_update_summary(
            repo_dir=repo_dir,
            previous_commit=previous_commit,
            current_commit=current_commit,
        )
        return 0
    except (CommandError, ValueError, OSError) as exc:
        print(f"Error: {exc}")
        return 1
