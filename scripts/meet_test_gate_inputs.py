"""Read-only immutable input snapshots for the explicitly selected Meet test gate."""

import hashlib
import os
import subprocess
from pathlib import Path

from scripts.hub_browser_test_evidence import canonical_digest, repository_revision, source_digest

ROOT_PATHS = (
    "agent",
    "worker",
    "ananta_contracts",
    "voice_runtime",
    "tests",
    "scripts",
    "docker/meet-media",
    "pyproject.toml",
    "AGENTS.md",
    "docs/contracts",
    "docs/planning-pipeline.md",
)


def snapshot_repository(root, paths):
    def git(*args):
        return subprocess.run(("git", *args), cwd=root, capture_output=True, check=True, timeout=15).stdout

    git("diff", "--quiet", "HEAD", "--", *paths)
    if git("ls-files", "--others", "--exclude-standard", "-z", "--", *paths):
        raise ValueError("meet_test_gate_untracked_sources")
    names = git("ls-files", "-z", "--", *paths).decode().split("\0")
    files = tuple(Path(name) for name in names if name)
    if not files:
        raise ValueError("meet_test_gate_sources_missing")
    return {"revision": repository_revision(root), "digest": source_digest(root, files)}, files


def frontend_digest(directory):
    root = Path(directory).resolve(strict=True)
    if not root.is_dir() or not (root / "index.html").is_file():
        raise ValueError("meet_test_gate_frontend_missing")
    records, total = [], 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("meet_test_gate_frontend_linked")
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if size > 32 * 1024**2 or total > 128 * 1024**2 or len(records) >= 4096:
            raise ValueError("meet_test_gate_frontend_budget")
        records.append([path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return canonical_digest(records)


def native_environment():
    # Pin only selected test inputs, never dump the inherited credential environment.
    names = (
        "PATH",
        "GOROOT",
        "LD_LIBRARY_PATH",
        "MEET_MULTI_WORKER_IMAGE",
        "MEET_TEST_PROXY_IMAGE",
        "MEET_TEST_PUBLIC_DIR",
        "ANANTA_MEET_DIALOG_CAPACITY",
        "ANANTA_MEET_DIALOG_CAPACITY_POOL",
    )
    return {name: os.environ.get(name) for name in names}
