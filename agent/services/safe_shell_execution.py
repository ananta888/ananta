"""Safe shell execution for deterministic runs (CLN-049).

Moved from agent/routes/deterministic_run.py so routes do not shell out
directly; the route handler stays a thin HTTP adapter over this service.
"""

from __future__ import annotations

import re
import subprocess
import time

_BLOCKED_RAW = [
    r"rm\s+-[rf]",
    r"\bdd\b.*of=",
    r"\bmkfs\b",
    r":\(\)\{",  # fork bomb
    r">\s*/dev/sd",
    r"chmod\s+000",
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"\bsudo\b",
]

BLOCKED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_RAW]

SAFE_WORKDIR = "/project-workspaces"


def is_blocked(cmd: str) -> str | None:
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(cmd):
            return f"Blocked pattern: {pattern.pattern}"
    return None


def run_shell(cmd: str, timeout: int) -> dict:
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=SAFE_WORKDIR,
        )
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[:4096],
            "stderr": result.stderr[:2048],
            "duration_ms": duration,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Timeout expired",
            "duration_ms": timeout * 1000,
            "success": False,
        }
    except Exception as exc:
        return {"exit_code": -1, "stdout": "", "stderr": str(exc), "duration_ms": 0, "success": False}
