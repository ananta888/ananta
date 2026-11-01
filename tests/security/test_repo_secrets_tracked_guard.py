"""Guards against secret material and runtime artefacts being re-tracked in git.

Regression tests for the CLN-M1/CLN-M2 repo-hygiene cleanup: once secret files
(``**/secrets/secret_key``), credential files (``login_payload.json``) and
runtime artefacts are removed from the git index, these tests fail if anyone
re-adds them. They also assert the opposite invariant: the tracked gitleaks
policy must stay present so the secret scan remains reproducible.

All checks are fully headless and self-contained (read-only ``git`` queries).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Files that must never be tracked in the repository index.
FORBIDDEN_TRACKED = (
    "frontend-angular/secrets/secret_key",
    "rag-helper/secrets/secret_key",
    "login_payload.json",
    "user.json",
    "test.db",
)

# Path suffixes that must never appear anywhere in the tracked index.
FORBIDDEN_TRACKED_SUFFIXES = (
    "/secrets/secret_key",
    "/secrets/key",
    ".egg-info/PKG-INFO",
)


def _git_ls_files(*paths: str) -> list[str]:
    """Return tracked files matching *paths* (read-only)."""
    cmd = ["git", "ls-files", "--", *paths]
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-files unavailable: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git_check_ignore(path: str) -> bool:
    """Return True when *path* is matched by .gitignore (read-only)."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # exit 0 => ignored, exit 1 => not ignored / not applicable
    return result.returncode == 0


def test_no_known_secret_files_are_tracked() -> None:
    """Explicit secret/credential files must not sit in the git index."""
    tracked = _git_ls_files(*FORBIDDEN_TRACKED)
    assert not tracked, (
        "Secret/credential files must not be tracked in git. "
        f"Remove them with `git rm --cached {tracked}` and keep them gitignored."
    )


def test_no_secret_key_suffix_is_tracked() -> None:
    """No ``**/secrets/secret_key``-style file may be tracked at any depth."""
    all_tracked = _git_ls_files()
    offenders = [
        f
        for f in all_tracked
        if any(f.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES)
    ]
    assert not offenders, f"Secret-looking files are tracked: {offenders}"


def test_gitleaks_policy_is_tracked() -> None:
    """The gitleaks config must stay tracked so the scan is reproducible."""
    tracked = _git_ls_files(".gitleaks.toml")
    assert tracked, (
        ".gitleaks.toml must be tracked (it defines the secret-scan policy). "
        "If ignored by `/.*`, re-include it via `!/.gitleaks.toml` after the "
        "dotfile catch-all."
    )


def test_gitignore_covers_nested_secret_keys() -> None:
    """.gitignore must ignore nested ``secrets/secret_key`` paths."""
    # Use a hypothetical path: the guard is about the pattern, not a real file.
    assert _git_check_ignore("x/y/secrets/secret_key"), (
        ".gitignore must contain a pattern covering nested secrets/secret_key "
        "(e.g. `**/secrets/**`)."
    )


def test_gitignore_covers_login_payload() -> None:
    """.gitignore must ignore ``login_payload.json`` so it cannot be re-added."""
    assert _git_check_ignore("any/login_payload.json"), (
        ".gitignore must ignore login_payload.json."
    )
