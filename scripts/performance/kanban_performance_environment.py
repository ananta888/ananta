"""Host, runtime and commit metadata of a formal Kanban performance run.

Collects the environment fingerprint (OS, CPU, memory, Python/Node/Playwright
and browser versions) that binds a baseline to comparable hosts, and the
candidate commit read from Git metadata files.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.performance.kanban_performance_io import (
        sha256_bytes as _sha256_bytes,
    )
    from scripts.performance.kanban_performance_validation import (
        SuiteValidationError,
    )
    from scripts.performance.kanban_performance_validation import (
        integer as _integer,
    )
    from scripts.performance.kanban_performance_validation import (
        mapping as _mapping,
    )
    from scripts.performance.kanban_performance_validation import (
        text as _text,
    )
except ModuleNotFoundError:
    from kanban_performance_io import (  # type: ignore
        sha256_bytes as _sha256_bytes,
    )
    from kanban_performance_validation import (  # type: ignore
        SuiteValidationError,
    )
    from kanban_performance_validation import (
        integer as _integer,
    )
    from kanban_performance_validation import (
        mapping as _mapping,
    )
    from kanban_performance_validation import (
        text as _text,
    )


ROOT = Path(__file__).resolve().parents[2]


def _command_version(command: list[str], *, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise SuiteValidationError(f"runtime_command_failed:{command[0]}")
    return _text(result.stdout, f"runtime_command_output:{command[0]}")


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return _text(line.partition(":")[2], "cpu_model")
    except OSError:
        pass
    return _text(platform.processor(), "cpu_model")


def _total_memory_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    raise SuiteValidationError("host_total_memory_unavailable")


def collect_environment(angular_runtime: dict[str, Any]) -> dict[str, Any]:
    source_node = _text(
        _mapping(angular_runtime.get("node"), "angular_node").get("version"),
        "angular_node_version",
    )
    source_playwright = _text(
        _mapping(
            angular_runtime.get("playwright"),
            "angular_playwright",
        ).get("version"),
        "angular_playwright_version",
    )
    node = _command_version(["node", "--version"])
    playwright = _command_version(
        [str(ROOT / "frontend-angular" / "node_modules" / ".bin" / "playwright"), "--version"],
        cwd=ROOT / "frontend-angular",
    )
    if node != source_node or playwright != source_playwright:
        raise SuiteValidationError("angular_runtime_environment_mismatch")
    browser = _mapping(angular_runtime.get("browser"), "angular_browser")
    environment = {
        "host": {
            "hostname": _text(platform.node(), "host_hostname"),
            "os": {
                "system": _text(platform.system(), "host_os_system"),
                "release": _text(platform.release(), "host_os_release"),
                "machine": _text(platform.machine(), "host_os_machine"),
            },
            "cpu": {
                "model": _cpu_model(),
                "logical_count": _integer(os.cpu_count(), "cpu_logical_count", minimum=1),
            },
            "memory": {"total_bytes": _total_memory_bytes()},
        },
        "runtimes": {
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "node": {"version": node},
            "playwright": {"version": playwright},
        },
        "browser": {
            "name": _text(browser.get("name"), "browser_name"),
            "version": _text(browser.get("version"), "browser_version"),
        },
    }
    environment["compatibility"] = {
        "os": environment["host"]["os"],
        "cpu": environment["host"]["cpu"],
        "memory": environment["host"]["memory"],
        "python": environment["runtimes"]["python"],
        "node": environment["runtimes"]["node"],
        "playwright": environment["runtimes"]["playwright"],
        "browser": environment["browser"],
    }
    environment["compatibility_sha256"] = _sha256_bytes(
        json.dumps(
            environment["compatibility"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return environment


def collect_commit(root: Path = ROOT) -> dict[str, str]:
    git_path = root / ".git"
    if git_path.is_file():
        content = git_path.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir:"):
            raise SuiteValidationError("gitdir_metadata_invalid")
        git_path = (root / content.partition(":")[2].strip()).resolve()
    head = (git_path / "HEAD").read_text(encoding="utf-8").strip()
    reference = "detached"
    if head.startswith("ref:"):
        reference = _text(head.partition(":")[2], "git_head_ref")
        ref_path = git_path / reference
        if ref_path.is_file():
            sha = ref_path.read_text(encoding="utf-8").strip()
        else:
            sha = ""
            packed = git_path / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        candidate, _, candidate_ref = line.partition(" ")
                        if candidate_ref == reference:
                            sha = candidate
                            break
    else:
        sha = head
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise SuiteValidationError("git_commit_sha_invalid")
    return {
        "sha": sha.lower(),
        "ref": reference,
        "source": "git_metadata_files",
    }
