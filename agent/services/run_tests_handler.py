"""run_tests hybrid handler (te-007).

Hybrid: deterministic execution, but LLM may have selected *which* tests to run.
Profile-gated: only commands explicitly allowed by the active test runner profile
are executed. Unknown runners are rejected before subprocess.

Profiles
--------
  default   : pytest (python projects)
  node      : jest / vitest / npm test
  rust      : cargo test
  go        : go test
  make      : make test / make check
  custom    : arbitrary command list supplied by caller — must be pre-approved

Registration
------------
Call ``register_run_tests_handler(app)`` from the app factory.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

from flask import Flask

from agent.services.task_handler_registry import register_task_handler


# ── Profiles ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TestRunnerProfile:
    name: str
    allowed_prefixes: tuple[str, ...]       # command must start with one of these
    default_command: str
    timeout_seconds: int = 120


_PROFILES: dict[str, TestRunnerProfile] = {
    "default": TestRunnerProfile(
        name="default",
        allowed_prefixes=("pytest", "python -m pytest", "python3 -m pytest"),
        default_command="pytest",
    ),
    "node": TestRunnerProfile(
        name="node",
        allowed_prefixes=("jest", "vitest", "npm test", "npm run test", "yarn test", "npx jest", "npx vitest"),
        default_command="npm test",
        timeout_seconds=180,
    ),
    "rust": TestRunnerProfile(
        name="rust",
        allowed_prefixes=("cargo test",),
        default_command="cargo test",
        timeout_seconds=300,
    ),
    "go": TestRunnerProfile(
        name="go",
        allowed_prefixes=("go test",),
        default_command="go test ./...",
        timeout_seconds=120,
    ),
    "make": TestRunnerProfile(
        name="make",
        allowed_prefixes=("make test", "make check"),
        default_command="make test",
    ),
}

DEFAULT_PROFILE = "default"


def _resolve_profile(name: str | None) -> TestRunnerProfile:
    return _PROFILES.get((name or DEFAULT_PROFILE).lower(), _PROFILES[DEFAULT_PROFILE])


def _command_allowed(cmd: str, profile: TestRunnerProfile) -> bool:
    cmd_stripped = cmd.strip()
    return any(cmd_stripped.startswith(prefix) for prefix in profile.allowed_prefixes)


# ── Handler ───────────────────────────────────────────────────────────────────

class RunTestsHandler:
    """Hybrid handler: runs a profile-gated test command deterministically."""

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        task = kwargs.get("task") or {}
        profile_name = task.get("test_runner_profile") or DEFAULT_PROFILE
        profile = _resolve_profile(profile_name)
        cmd = task.get("command") or profile.default_command
        cwd = task.get("cwd") or "."
        return {
            "proposal_id": "run_tests-proposal",
            "strategy_id": "deterministic_handler",
            "command": cmd,
            "tool_calls": [{"name": "run_tests", "arguments": {"command": cmd, "cwd": cwd}}],
            "expected_artifacts": [],
            "safety_flags": {"read_only": False, "mutates_filesystem": False, "profile": profile_name},
            "metadata": {"test_runner_profile": profile_name, "cwd": cwd},
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        task = kwargs.get("task") or {}
        profile_name = task.get("test_runner_profile") or DEFAULT_PROFILE
        profile = _resolve_profile(profile_name)
        cmd = (task.get("command") or profile.default_command).strip()
        cwd = task.get("cwd") or "."
        extra_args: list[str] = task.get("extra_args") or []

        if not _command_allowed(cmd, profile):
            return {
                "output": f"blocked: command '{cmd}' not allowed by profile '{profile.name}'",
                "exit_code": 1,
                "blocked": True,
                "policy_violation": "run_tests_profile_gate",
            }

        try:
            parts = shlex.split(cmd) + [str(a) for a in extra_args]
            r = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=profile.timeout_seconds,
                cwd=cwd,
            )
            combined = r.stdout + (("\n--- stderr ---\n" + r.stderr) if r.stderr.strip() else "")
            return {
                "output": combined,
                "exit_code": r.returncode,
                "profile": profile.name,
                "command": cmd,
            }
        except subprocess.TimeoutExpired:
            return {"output": f"timeout after {profile.timeout_seconds}s", "exit_code": 1, "timeout": True}
        except FileNotFoundError:
            return {"output": f"runner not found: {shlex.split(cmd)[0]}", "exit_code": 127}
        except Exception as exc:
            return {"output": str(exc), "exit_code": 1, "error": type(exc).__name__}


def register_run_tests_handler(app: Flask | None = None) -> None:
    register_task_handler(
        "run_tests",
        RunTestsHandler(),
        app,
        capabilities=["run_tests"],
        safety_flags={"read_only": False, "mutates_filesystem": False},
    )
