"""Conservative stale-build preflight, not source provenance or release evidence."""

import os
import subprocess
from pathlib import Path


def require_current_browser_build(repository, public_dir=None):
    repository = Path(repository)
    directory = Path(public_dir) if public_dir is not None else repository / "dist/browser"
    if not directory.is_absolute():
        raise ValueError("meet_test_browser_build_path_invalid")
    try:
        index = directory / "index.html"
        if not index.is_file():
            raise ValueError("meet_test_browser_build_missing")
        built_at = index.stat().st_mtime_ns
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "frontend",
                "src",
                "angular.json",
                "tsconfig.json",
                "package.json",
                "package-lock.json",
            ],
            cwd=repository,
            capture_output=True,
            check=True,
            timeout=5,
        )
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("meet_test_browser_inputs_excessive")
        names = {os.fsdecode(value) for value in result.stdout.split(b"\0") if value}
        if not {"angular.json", "package.json", "frontend/src/main.ts"} <= names:
            raise ValueError("meet_test_browser_inputs_missing")
        for name in names:
            source = repository / name
            if not source.is_file() or source.stat().st_mtime_ns > built_at:
                raise ValueError("meet_test_browser_build_stale_rebuild_in_private_directory")
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("meet_test_browser_build_unavailable") from error
    return {"freshness_check": "mtime_only", "source_files": len(names), "production_release_evidence": False}
