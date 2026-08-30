#!/usr/bin/env python3
"""Produce commit-bound release evidence for the Kanban/model dashboard.

The command allowlist is code, not user input. Evidence is produced after the
candidate commit exists and is an external CI/release artifact; an evidence
file must never be an input to, or a tracked file in, that candidate commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCHEMA = "ananta.kanban-model-dashboard.evidence.v1"
PRODUCER_NAME = "kanban-model-dashboard-evidence-producer"
PRODUCER_VERSION = "1"
ARTIFACT_BOUNDARY = "post_candidate_ci_release_artifact"
EVIDENCE_RELATIVE_DIR = PurePosixPath(
    "artifacts/e2e/kanban-model-dashboard"
)
REQUIRED_SUITES = (
    "contract",
    "backend",
    "angular",
    "tui",
    "security",
    "accessibility",
    "performance",
)
MAX_INPUT_COUNT = 160
MAX_INPUT_BYTES = 12_000_000
MAX_TOTAL_INPUT_BYTES = 64_000_000
MAX_RESULT_BYTES = 4_000_000
MAX_COMMAND_OUTPUT_BYTES = 8_000_000
MAX_EVIDENCE_BYTES = 2_000_000
MAX_PATH_LENGTH = 512
MAX_COMMAND_TOKENS = 128
MAX_TOKEN_LENGTH = 1_024
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ANSI_PATTERN = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


class EvidenceProducerError(RuntimeError):
    """Raised when evidence cannot be produced safely."""


class EvidenceBlocked(EvidenceProducerError):
    """A fail-closed precondition that prevents suite execution."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: str = "."
    env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: int = 600
    validator: str = "pytest"
    minimum_passed: int = 1
    result_path: str | None = None


@dataclass(frozen=True, slots=True)
class SuiteSpec:
    suite: str
    commands: tuple[CommandSpec, ...]
    inputs: tuple[str, ...]


COMMON_INPUTS = (
    "scripts/run_kanban_model_dashboard_evidence.py",
    "scripts/run_kanban_model_dashboard_release_gate.py",
    "config/test-profiles/kanban-model-dashboard/release-gate.v1.json",
    "pyproject.toml",
    "requirements.txt",
)


def _suite(
    suite: str,
    *,
    commands: tuple[CommandSpec, ...],
    inputs: tuple[str, ...],
) -> SuiteSpec:
    return SuiteSpec(
        suite=suite,
        commands=commands,
        inputs=tuple(dict.fromkeys((*COMMON_INPUTS, *inputs))),
    )


SUITE_SPECS: Mapping[str, SuiteSpec] = {
    "contract": _suite(
        "contract",
        commands=(
            CommandSpec(
                argv=(
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_kanban_contracts.py",
                    "tests/test_model_catalog_contract.py",
                    "tests/test_kanban_model_dashboard_shared_contract.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_shared_contract_fixture.py",
                ),
                minimum_passed=4,
            ),
        ),
        inputs=(
            "ananta_contracts/kanban.py",
            "ananta_contracts/kanban_events.py",
            "ananta_contracts/model_catalog.py",
            "tests/test_kanban_contracts.py",
            "tests/test_model_catalog_contract.py",
            "tests/test_kanban_model_dashboard_shared_contract.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_shared_contract_fixture.py",
        ),
    ),
    "backend": _suite(
        "backend",
        commands=(
            CommandSpec(
                argv=(
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_kanban_projection_service.py",
                    "tests/test_kanban_api.py",
                    "tests/test_kanban_durable_outbox.py",
                    "tests/test_kanban_outbox_migration.py",
                    "tests/test_kanban_event_stream_service.py",
                    "tests/test_kanban_event_api.py",
                    "tests/test_model_catalog_service.py",
                    "tests/test_model_catalog_api.py",
                ),
                timeout_seconds=900,
                minimum_passed=12,
            ),
        ),
        inputs=(
            "agent/db_models/kanban_projection.py",
            "agent/repositories/kanban_projection.py",
            "agent/services/kanban_projection_service.py",
            "agent/services/kanban_event_stream_service.py",
            "agent/services/model_catalog_service.py",
            "agent/routes/tasks/kanban.py",
            "agent/routes/config/providers.py",
            "migrations/versions/b8d0f2a4c6e8_add_kanban_event_outbox.py",
            "tests/test_kanban_projection_service.py",
            "tests/test_kanban_api.py",
            "tests/test_kanban_durable_outbox.py",
            "tests/test_kanban_outbox_migration.py",
            "tests/test_kanban_event_stream_service.py",
            "tests/test_kanban_event_api.py",
            "tests/test_model_catalog_service.py",
            "tests/test_model_catalog_api.py",
        ),
    ),
    "angular": _suite(
        "angular",
        commands=(
            CommandSpec(
                argv=(
                    "{npx}",
                    "vitest",
                    "run",
                    "src/app/contracts/"
                    "kanban-model-dashboard.fixture.spec.ts",
                    "src/app/features/tasks/kanban/kanban.store.spec.ts",
                    "src/app/features/system/model-dashboard/"
                    "model-catalog.client.spec.ts",
                ),
                cwd="frontend-angular",
                timeout_seconds=600,
                validator="vitest",
                minimum_passed=3,
            ),
        ),
        inputs=(
            "frontend-angular/package.json",
            "frontend-angular/package-lock.json",
            "frontend-angular/src/app/contracts/"
            "kanban-model-dashboard.fixture.spec.ts",
            "frontend-angular/src/app/features/tasks/kanban/"
            "kanban-api.client.ts",
            "frontend-angular/src/app/features/tasks/kanban/kanban.store.ts",
            "frontend-angular/src/app/features/tasks/kanban/"
            "kanban.store.spec.ts",
            "frontend-angular/src/app/features/system/model-dashboard/"
            "model-catalog.client.ts",
            "frontend-angular/src/app/features/system/model-dashboard/"
            "model-catalog.client.spec.ts",
            "frontend-angular/src/app/features/system/model-dashboard/"
            "model-dashboard.component.ts",
            "frontend-angular/src/app/features/system/model-dashboard/"
            "model-dashboard.store.ts",
        ),
    ),
    "tui": _suite(
        "tui",
        commands=(
            CommandSpec(
                argv=(
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_atomic_snapshot.py",
                    "tests/client_surfaces/operator_tui/test_dashboard_auth.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_autoload.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_event_transport.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_http_adapter.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_live_lifecycle.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_live_sync.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_dashboard_surfaces.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_external_window_view_models.py",
                    "tests/client_surfaces/operator_tui/"
                    "test_kanban_windowing.py",
                    "tests/e2e/test_tui_kanban_pty_resize.py",
                ),
                env=(("RUN_INTEGRATION_TESTS", "1"),),
                timeout_seconds=900,
                minimum_passed=10,
            ),
        ),
        inputs=(
            "client_surfaces/operator_tui/dashboard_surfaces.py",
            "client_surfaces/operator_tui/dashboard_http_adapter.py",
            "client_surfaces/operator_tui/dashboard_autoload.py",
            "client_surfaces/operator_tui/interactive.py",
            "client_surfaces/operator_tui/renderer.py",
            "scripts/e2e/tui_kanban_pty_resize.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_atomic_snapshot.py",
            "tests/client_surfaces/operator_tui/test_dashboard_auth.py",
            "tests/client_surfaces/operator_tui/test_dashboard_autoload.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_event_transport.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_http_adapter.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_live_lifecycle.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_live_sync.py",
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_surfaces.py",
            "tests/client_surfaces/operator_tui/"
            "test_external_window_view_models.py",
            "tests/client_surfaces/operator_tui/test_kanban_windowing.py",
            "tests/e2e/test_tui_kanban_pty_resize.py",
        ),
    ),
    "security": _suite(
        "security",
        commands=(
            CommandSpec(
                argv=(
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    "tests/security/test_kanban_model_surface_security.py",
                    "tests/test_surface_rate_limits.py",
                ),
                timeout_seconds=600,
                minimum_passed=10,
            ),
        ),
        inputs=(
            "agent/services/kanban_authorization_service.py",
            "agent/services/surface_rate_limit_policy.py",
            "agent/routes/tasks/kanban.py",
            "agent/routes/config/providers.py",
            "tests/security/test_kanban_model_surface_security.py",
            "tests/test_surface_rate_limits.py",
        ),
    ),
    "accessibility": _suite(
        "accessibility",
        commands=(
            CommandSpec(
                argv=(
                    "{npx}",
                    "playwright",
                    "test",
                    "tests/kanban-model-dashboard.spec.ts",
                    "--retries=0",
                    "--workers=1",
                    "--reporter=json",
                ),
                cwd="frontend-angular",
                env=(
                    ("E2E_BROWSERS", "chromium,firefox"),
                    ("E2E_PORT", "4217"),
                    (
                        "E2E_RESULTS_DIR",
                        "/tmp/ananta-kanban-model-dashboard-accessibility-v1",
                    ),
                ),
                timeout_seconds=1_200,
                validator="playwright",
                minimum_passed=4,
            ),
        ),
        inputs=(
            "frontend-angular/package.json",
            "frontend-angular/package-lock.json",
            "frontend-angular/playwright.config.ts",
            "frontend-angular/tests/kanban-model-dashboard.spec.ts",
            "frontend-angular/src/app/features/tasks/kanban/"
            "kanban.store.ts",
            "frontend-angular/src/app/features/system/model-dashboard/"
            "model-dashboard.component.ts",
        ),
    ),
    "performance": _suite(
        "performance",
        commands=(
            CommandSpec(
                argv=(
                    "{python}",
                    "scripts/performance/"
                    "run_kanban_projection_local_diagnostic.py",
                    "--profile",
                    "config/test-profiles/kanban-model-dashboard/"
                    "local-performance.v1.json",
                    "--output",
                    "artifacts/kanban-local-performance-diagnostic.json",
                ),
                timeout_seconds=900,
                validator="performance_backend",
                result_path=(
                    "artifacts/kanban-local-performance-diagnostic.json"
                ),
            ),
            CommandSpec(
                argv=(
                    "{python}",
                    "scripts/performance/"
                    "run_angular_kanban_local_diagnostic.py",
                    "--output",
                    "artifacts/angular-kanban-local-performance-diagnostic.json",
                ),
                timeout_seconds=1_200,
                validator="performance_angular",
                result_path=(
                    "artifacts/angular-kanban-local-performance-diagnostic.json"
                ),
            ),
            CommandSpec(
                argv=(
                    "{python}",
                    "scripts/performance/"
                    "run_tui_kanban_local_diagnostic.py",
                    "--profile",
                    "config/test-profiles/kanban-model-dashboard/"
                    "local-tui-performance.v1.json",
                    "--output",
                    "artifacts/tui-kanban-local-performance-diagnostic.json",
                ),
                timeout_seconds=900,
                validator="performance_tui",
                result_path=(
                    "artifacts/tui-kanban-local-performance-diagnostic.json"
                ),
            ),
            CommandSpec(
                argv=(
                    "{python}",
                    "scripts/e2e/tui_kanban_pty_resize.py",
                    "--cards",
                    "1000",
                    "--timeout-seconds",
                    "15",
                    "--output",
                    "artifacts/tui-kanban-pty-resize-local-diagnostic.json",
                ),
                timeout_seconds=300,
                validator="performance_pty",
                result_path=(
                    "artifacts/tui-kanban-pty-resize-local-diagnostic.json"
                ),
            ),
            CommandSpec(
                argv=(
                    "{python}",
                    "scripts/performance/"
                    "run_kanban_model_dashboard_performance_suite.py",
                    "evaluate",
                    "--profile",
                    "config/test-profiles/kanban-model-dashboard/"
                    "formal-performance.v1.json",
                    "--baseline",
                    "config/test-profiles/kanban-model-dashboard/baselines/"
                    "formal-performance-approved.v1.json",
                    "--backend-result",
                    "artifacts/kanban-local-performance-diagnostic.json",
                    "--angular-result",
                    "artifacts/angular-kanban-local-performance-diagnostic.json",
                    "--tui-result",
                    "artifacts/tui-kanban-local-performance-diagnostic.json",
                    "--pty-result",
                    "artifacts/tui-kanban-pty-resize-local-diagnostic.json",
                    "--output",
                    "artifacts/test-gates/"
                    "kanban-model-dashboard-performance-gate.v1.json",
                ),
                timeout_seconds=300,
                validator="performance_gate",
                result_path=(
                    "artifacts/test-gates/"
                    "kanban-model-dashboard-performance-gate.v1.json"
                ),
            ),
        ),
        inputs=(
            "scripts/performance/run_kanban_projection_local_diagnostic.py",
            "scripts/performance/run_angular_kanban_local_diagnostic.py",
            "scripts/performance/run_tui_kanban_local_diagnostic.py",
            "scripts/performance/"
            "run_kanban_model_dashboard_performance_suite.py",
            "scripts/performance/kanban_baseline_approval_policy.py",
            "scripts/e2e/tui_kanban_pty_resize.py",
            "frontend-angular/tests/kanban-performance.local.spec.ts",
            "config/test-profiles/kanban-model-dashboard/"
            "local-performance.v1.json",
            "config/test-profiles/kanban-model-dashboard/"
            "local-tui-performance.v1.json",
            "config/test-profiles/kanban-model-dashboard/"
            "formal-performance.v1.json",
            "config/test-profiles/kanban-model-dashboard/"
            "baseline-approval-policy.v1.json",
            "config/test-profiles/kanban-model-dashboard/baselines/"
            "formal-performance-approved.v1.json",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    failure_code: str | None = None


class CommandExecutorPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str],
        timeout_seconds: int,
    ) -> ExecutionResult: ...


class CandidateSourcePort(Protocol):
    def current_commit(self) -> str: ...
    def path_exists(self, commit_sha: str, relative_path: str) -> bool: ...
    def read_path(
        self,
        commit_sha: str,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> bytes | None: ...


class SubprocessCommandExecutor:
    """Execute fixed argv without a shell and without inherited test options."""

    _PASSTHROUGH = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str],
        timeout_seconds: int,
    ) -> ExecutionResult:
        environment = {
            name: os.environ[name]
            for name in self._PASSTHROUGH
            if name in os.environ
        }
        environment.update(
            {
                "CI": "true",
                "NO_COLOR": "1",
                "PYTHONHASHSEED": "0",
                **env_overrides,
            }
        )
        try:
            completed = subprocess.run(
                list(argv),
                cwd=str(cwd),
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=None,
                failure_code="command_executable_missing",
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                exit_code=None,
                stdout=bytes(exc.stdout or b""),
                stderr=bytes(exc.stderr or b""),
                failure_code="command_timeout",
            )
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class GitCandidateSource:
    """Read candidate blobs with argv-only Git plumbing commands."""

    def __init__(self, root: Path):
        self._root = root
        executable = shutil.which("git")
        if executable is None:
            raise EvidenceBlocked("candidate_git_unavailable")
        self._git = executable

    def _run(
        self,
        *arguments: str,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            [self._git, "-C", str(self._root), *arguments],
            capture_output=True,
            check=False,
            shell=False,
        )
        if completed.returncode != 0 and not allow_failure:
            raise EvidenceBlocked("candidate_git_read_failed")
        return completed

    def current_commit(self) -> str:
        value = self._run("rev-parse", "--verify", "HEAD").stdout.decode(
            "ascii",
            errors="strict",
        ).strip()
        if not SHA_PATTERN.fullmatch(value):
            raise EvidenceBlocked("candidate_checkout_sha_invalid")
        return value

    def path_exists(self, commit_sha: str, relative_path: str) -> bool:
        return (
            self._run(
                "cat-file",
                "-e",
                f"{commit_sha}:{relative_path}",
                allow_failure=True,
            ).returncode
            == 0
        )

    def read_path(
        self,
        commit_sha: str,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> bytes | None:
        object_name = f"{commit_sha}:{relative_path}"
        size_result = self._run(
            "cat-file",
            "-s",
            object_name,
            allow_failure=True,
        )
        if size_result.returncode != 0:
            return None
        try:
            size = int(size_result.stdout.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise EvidenceBlocked("candidate_blob_size_invalid") from exc
        if size < 0 or size > max_bytes:
            raise EvidenceBlocked("candidate_blob_oversized")
        content = self._run("show", object_name).stdout
        if len(content) != size:
            raise EvidenceBlocked("candidate_blob_size_mismatch")
        return content


def _safe_relative_path(value: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or "\\" in value
        or "\x00" in value
    ):
        raise EvidenceProducerError("unsafe_relative_path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidenceProducerError("unsafe_relative_path")
    return path


def _safe_existing_file(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> Path:
    relative = _safe_relative_path(relative_path)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceBlocked("input_symlink_unsafe")
    if not current.exists():
        raise EvidenceBlocked("input_missing")
    if not current.is_file():
        raise EvidenceBlocked("input_not_regular_file")
    size = current.stat().st_size
    if size < 0 or size > max_bytes:
        raise EvidenceBlocked("input_oversized")
    return current


def _safe_output_path(root: Path, suite: str) -> tuple[Path, str]:
    if suite not in REQUIRED_SUITES:
        raise EvidenceProducerError("suite_not_allowlisted")
    if root.is_symlink():
        raise EvidenceProducerError("repository_root_symlink_unsafe")
    directory = root
    for part in EVIDENCE_RELATIVE_DIR.parts:
        directory = directory / part
        if directory.exists() and directory.is_symlink():
            raise EvidenceProducerError("evidence_directory_symlink_unsafe")
        directory.mkdir(exist_ok=True)
    output = directory / f"{suite}.json"
    if output.exists() and output.is_symlink():
        raise EvidenceProducerError("evidence_output_symlink_unsafe")
    relative = str(EVIDENCE_RELATIVE_DIR / f"{suite}.json")
    return output, relative


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise EvidenceProducerError("timezone_aware_clock_required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def suite_allowlist_sha256(suite: str) -> str:
    spec = SUITE_SPECS.get(suite)
    if spec is None:
        raise ValueError("suite_not_allowlisted")
    canonical = json.dumps(
        asdict(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return _sha256_bytes(canonical)


def _materialize_argv(
    argv: Sequence[str],
    *,
    python_executable: str,
    npx_executable: str,
) -> tuple[str, ...]:
    replacements = {
        "{python}": python_executable,
        "{npx}": npx_executable,
    }
    return tuple(replacements.get(token, token) for token in argv)


def recorded_commands_match_allowlist(
    suite: str,
    commands: object,
    runtime: object,
) -> bool:
    spec = SUITE_SPECS.get(suite)
    if spec is None or not isinstance(commands, list) or not isinstance(
        runtime,
        Mapping,
    ):
        return False
    python_executable = runtime.get("python_executable")
    npx_executable = runtime.get("npx_executable")
    if not isinstance(python_executable, str) or not isinstance(
        npx_executable,
        str,
    ):
        return False
    if len(commands) != len(spec.commands):
        return False
    for expected, recorded in zip(spec.commands, commands, strict=True):
        if not isinstance(recorded, Mapping):
            return False
        actual_argv = _materialize_argv(
            expected.argv,
            python_executable=python_executable,
            npx_executable=npx_executable,
        )
        if recorded.get("allowlist_argv") != list(expected.argv):
            return False
        if recorded.get("argv") != list(actual_argv):
            return False
        if recorded.get("cwd") != expected.cwd:
            return False
        if recorded.get("env_overrides") != dict(expected.env):
            return False
        if recorded.get("timeout_seconds") != expected.timeout_seconds:
            return False
    return True


def _validate_specs() -> None:
    if tuple(SUITE_SPECS) != REQUIRED_SUITES:
        raise EvidenceProducerError("suite_allowlist_must_contain_exactly_seven")
    allowed_validators = {
        "pytest",
        "vitest",
        "playwright",
        "performance_backend",
        "performance_angular",
        "performance_tui",
        "performance_pty",
        "performance_gate",
    }
    for suite, spec in SUITE_SPECS.items():
        if spec.suite != suite or not spec.commands or not spec.inputs:
            raise EvidenceProducerError("suite_allowlist_invalid")
        if len(spec.inputs) > MAX_INPUT_COUNT:
            raise EvidenceProducerError("suite_input_allowlist_oversized")
        for path in spec.inputs:
            _safe_relative_path(path)
        for command in spec.commands:
            if (
                not command.argv
                or len(command.argv) > MAX_COMMAND_TOKENS
                or command.validator not in allowed_validators
                or command.timeout_seconds < 1
            ):
                raise EvidenceProducerError("suite_command_allowlist_invalid")
            for token in command.argv:
                if (
                    not token
                    or len(token) > MAX_TOKEN_LENGTH
                    or "\x00" in token
                    or "\n" in token
                    or "\r" in token
                ):
                    raise EvidenceProducerError(
                        "suite_command_token_invalid"
                    )
            _safe_relative_path(command.cwd) if command.cwd != "." else None
            if command.result_path:
                _safe_relative_path(command.result_path)


def _summary_count(raw: bytes, label: bytes) -> int | None:
    clean = ANSI_PATTERN.sub(b"", raw)
    match = re.search(rb"(\d+)\s+" + label, clean, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _validate_pytest(
    spec: CommandSpec,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, Any]:
    combined = stdout + b"\n" + stderr
    passed = _summary_count(combined, b"passed")
    forbidden = any(
        (_summary_count(combined, label) or 0) > 0
        for label in (b"failed", b"errors?", b"skipped", b"xfailed", b"xpassed")
    )
    if passed is None or passed < spec.minimum_passed or forbidden:
        raise EvidenceProducerError("pytest_result_requirements_failed")
    return {"passed": passed, "minimum_passed": spec.minimum_passed}


def _validate_vitest(
    spec: CommandSpec,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, Any]:
    combined = ANSI_PATTERN.sub(b"", stdout + b"\n" + stderr)
    match = re.search(
        rb"Tests\s+(\d+)\s+passed",
        combined,
        flags=re.IGNORECASE,
    )
    passed = int(match.group(1)) if match else None
    if (
        passed is None
        or passed < spec.minimum_passed
        or re.search(rb"\b[1-9]\d*\s+failed\b", combined, re.IGNORECASE)
    ):
        raise EvidenceProducerError("vitest_result_requirements_failed")
    return {"passed": passed, "minimum_passed": spec.minimum_passed}


def _validate_playwright(
    spec: CommandSpec,
    stdout: bytes,
    _stderr: bytes,
) -> dict[str, Any]:
    try:
        decoded = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceProducerError("playwright_json_result_invalid") from exc
    decoder = json.JSONDecoder()
    reports: list[Mapping[str, Any]] = []
    for index, character in enumerate(decoded):
        if character != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(decoded[index:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, Mapping)
            and isinstance(candidate.get("stats"), Mapping)
            and isinstance(candidate.get("config"), Mapping)
            and isinstance(candidate.get("suites"), list)
        ):
            reports.append(candidate)
    if len(reports) != 1:
        raise EvidenceProducerError("playwright_json_result_invalid")
    report = reports[0]
    stats = report.get("stats")
    config = report.get("config")
    if not isinstance(stats, Mapping) or not isinstance(config, Mapping):
        raise EvidenceProducerError("playwright_json_result_invalid")
    expected = stats.get("expected")
    unexpected = stats.get("unexpected")
    skipped = stats.get("skipped")
    projects = config.get("projects")
    project_names = {
        item.get("name")
        for item in projects
        if isinstance(projects, list) and isinstance(item, Mapping)
    }
    if (
        not isinstance(expected, int)
        or expected < spec.minimum_passed
        or unexpected != 0
        or skipped != 0
        or not {"chromium", "firefox"}.issubset(project_names)
    ):
        raise EvidenceProducerError(
            "playwright_result_requirements_failed"
        )
    return {
        "expected": expected,
        "unexpected": unexpected,
        "skipped": skipped,
        "projects": sorted(str(name) for name in project_names),
    }


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for name in path:
        if not isinstance(current, Mapping) or name not in current:
            return None
        current = current[name]
    return current


def _read_result_json(root: Path, relative_path: str) -> tuple[dict[str, Any], str]:
    path = _safe_existing_file(
        root,
        relative_path,
        max_bytes=MAX_RESULT_BYTES,
    )
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceProducerError("command_result_json_invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceProducerError("command_result_json_invalid")
    return value, _sha256_bytes(raw)


def _validate_performance_result(
    validator: str,
    value: Mapping[str, Any],
    candidate_sha: str,
) -> dict[str, Any]:
    schema = value.get("schema")
    if not isinstance(schema, str) or not schema:
        raise EvidenceProducerError("performance_result_schema_invalid")
    if validator == "performance_backend":
        valid = (
            schema == "ananta.kanban-local-performance-diagnostic.v1"
            and value.get("release_evidence") is False
            and value.get("formal_gate_eligible") is False
            and _nested(value, "absolute_evaluation", "within_budget") is True
        )
    elif validator == "performance_angular":
        valid = (
            schema == "ananta.kanban-performance-local-diagnostic.v1"
            and value.get("release_evidence") is False
        )
    elif validator == "performance_tui":
        valid = (
            schema
            == "ananta.tui-kanban-local-performance-diagnostic.v1"
            and value.get("release_evidence") is False
            and value.get("formal_gate_eligible") is False
            and _nested(value, "absolute_evaluation", "within_budget") is True
        )
    elif validator == "performance_pty":
        valid = (
            value.get("release_evidence") is False
            and value.get("formal_gate_eligible") is False
            and value.get("card_count") == 1000
            and isinstance(value.get("resize_measurements"), list)
        )
    else:
        profile_sha = _nested(value, "profile", "sha256")
        valid = (
            value.get("status") == "passed"
            and value.get("release_evidence") is True
            and value.get("formal_gate_eligible") is True
            and value.get("blockers") == []
            and _nested(value, "commit", "sha") == candidate_sha
            and isinstance(value.get("environment"), Mapping)
            and isinstance(value.get("measurements"), Mapping)
            and isinstance(value.get("absolute_evaluation"), Mapping)
            and isinstance(value.get("baseline_evaluation"), Mapping)
            and isinstance(profile_sha, str)
            and HEX_64_PATTERN.fullmatch(profile_sha) is not None
        )
    if not valid:
        raise EvidenceProducerError(
            f"{validator}_result_requirements_failed"
        )
    return {"schema": schema, "requirements_satisfied": True}


def _validate_command_result(
    spec: CommandSpec,
    execution: ExecutionResult,
    *,
    root: Path,
    candidate_sha: str,
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    if len(execution.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(
        execution.stderr
    ) > MAX_COMMAND_OUTPUT_BYTES:
        raise EvidenceProducerError("command_output_oversized")
    if execution.failure_code:
        raise EvidenceProducerError(execution.failure_code)
    if execution.exit_code != 0:
        raise EvidenceProducerError("command_exit_nonzero")
    if spec.validator == "pytest":
        return _validate_pytest(spec, execution.stdout, execution.stderr), None
    if spec.validator == "vitest":
        return _validate_vitest(spec, execution.stdout, execution.stderr), None
    if spec.validator == "playwright":
        return (
            _validate_playwright(spec, execution.stdout, execution.stderr),
            None,
        )
    if spec.result_path is None:
        raise EvidenceProducerError("command_result_path_missing")
    value, digest = _read_result_json(root, spec.result_path)
    summary = _validate_performance_result(
        spec.validator,
        value,
        candidate_sha,
    )
    return summary, (spec.result_path, digest)


def _prepare_result_path(root: Path, relative_path: str) -> None:
    relative = _safe_relative_path(relative_path)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise EvidenceBlocked("result_directory_symlink_unsafe")
        current.mkdir(exist_ok=True)
    result = current / relative.name
    if result.exists():
        if result.is_symlink() or not result.is_file():
            raise EvidenceBlocked("result_path_unsafe")
        result.unlink()


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    replace: Callable[[str | bytes | Path, str | bytes | Path], None] = os.replace,
) -> None:
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise EvidenceProducerError("evidence_output_oversized")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class EvidenceProducer:
    def __init__(
        self,
        *,
        root: Path = ROOT,
        candidate_source: CandidateSourcePort | None = None,
        executor: CommandExecutorPort | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        python_executable: str | None = None,
        npx_executable: str | None = None,
    ) -> None:
        _validate_specs()
        self._root = root.resolve()
        self._source = candidate_source or GitCandidateSource(self._root)
        self._executor = executor or SubprocessCommandExecutor()
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._python = python_executable or sys.executable
        self._npx = npx_executable or (shutil.which("npx") or "")

    def _runtime(self) -> dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable": self._python,
            "npx_executable": self._npx,
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "ci": str(os.environ.get("CI") or "").lower()
            in {"1", "true", "yes", "on"},
        }

    def _verify_inputs(
        self,
        spec: SuiteSpec,
        candidate_sha: str,
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        hashes: dict[str, str] = {}
        descriptors: list[dict[str, Any]] = []
        total = 0
        for relative_path in spec.inputs:
            path = _safe_existing_file(
                self._root,
                relative_path,
                max_bytes=MAX_INPUT_BYTES,
            )
            actual = path.read_bytes()
            total += len(actual)
            if total > MAX_TOTAL_INPUT_BYTES:
                raise EvidenceBlocked("input_total_size_exceeded")
            candidate = self._source.read_path(
                candidate_sha,
                relative_path,
                max_bytes=MAX_INPUT_BYTES,
            )
            if candidate is None:
                raise EvidenceBlocked("input_not_in_candidate")
            actual_hash = _sha256_bytes(actual)
            candidate_hash = _sha256_bytes(candidate)
            if actual_hash != candidate_hash:
                raise EvidenceBlocked("input_hash_drift")
            hashes[relative_path] = actual_hash
            descriptors.append(
                {
                    "path": relative_path,
                    "size_bytes": len(actual),
                    "sha256": actual_hash,
                    "candidate_sha256": candidate_hash,
                }
            )
        return hashes, descriptors

    def _base_payload(
        self,
        *,
        spec: SuiteSpec,
        candidate_sha: str,
        started_at: datetime,
    ) -> dict[str, Any]:
        return {
            "schema": EVIDENCE_SCHEMA,
            "suite": spec.suite,
            "status": "blocked",
            "commit_sha": candidate_sha,
            "started_at": _utc(started_at),
            "produced_at": _utc(started_at),
            "producer": {
                "name": PRODUCER_NAME,
                "version": PRODUCER_VERSION,
                "artifact_boundary": ARTIFACT_BOUNDARY,
                "allowlist_sha256": suite_allowlist_sha256(spec.suite),
            },
            "candidate": {
                "commit_sha": candidate_sha,
                "checkout_commit_sha": None,
                "verified": False,
                "input_blobs_verified": False,
                "evidence_path_not_in_candidate": False,
            },
            "runtime": self._runtime(),
            "input_hashes": {},
            "inputs": [],
            "commands": [],
            "result_hashes": {},
            "reason_codes": [],
        }

    def produce_suite(
        self,
        *,
        suite: str,
        candidate_sha: str,
    ) -> dict[str, Any]:
        spec = SUITE_SPECS.get(suite)
        if spec is None:
            raise EvidenceProducerError("suite_not_allowlisted")
        if not SHA_PATTERN.fullmatch(candidate_sha):
            raise EvidenceProducerError("candidate_sha_invalid")
        output, output_relative = _safe_output_path(self._root, suite)
        if output_relative in spec.inputs:
            raise EvidenceProducerError("evidence_sha_circularity")
        if self._source.path_exists(candidate_sha, output_relative):
            raise EvidenceProducerError("evidence_output_in_candidate")

        started_at = self._clock()
        payload = self._base_payload(
            spec=spec,
            candidate_sha=candidate_sha,
            started_at=started_at,
        )
        try:
            checkout = self._source.current_commit()
            payload["candidate"]["checkout_commit_sha"] = checkout
            if checkout != candidate_sha:
                raise EvidenceBlocked("candidate_checkout_mismatch")
            payload["candidate"]["verified"] = True
            payload["candidate"]["evidence_path_not_in_candidate"] = True
            input_hashes, inputs = self._verify_inputs(spec, candidate_sha)
            payload["input_hashes"] = input_hashes
            payload["inputs"] = inputs
            payload["candidate"]["input_blobs_verified"] = True
            if not self._npx and any(
                "{npx}" in command.argv for command in spec.commands
            ):
                raise EvidenceBlocked("npx_executable_missing")

            for index, command in enumerate(spec.commands):
                if command.result_path:
                    _prepare_result_path(self._root, command.result_path)
                argv = _materialize_argv(
                    command.argv,
                    python_executable=self._python,
                    npx_executable=self._npx,
                )
                command_started = self._clock()
                monotonic_started = self._monotonic()
                execution = self._executor.run(
                    argv,
                    cwd=(
                        self._root
                        if command.cwd == "."
                        else self._root / command.cwd
                    ),
                    env_overrides=dict(command.env),
                    timeout_seconds=command.timeout_seconds,
                )
                command_finished = self._clock()
                duration_ms = max(
                    0.0,
                    (self._monotonic() - monotonic_started) * 1000.0,
                )
                record: dict[str, Any] = {
                    "index": index,
                    "allowlist_argv": list(command.argv),
                    "argv": list(argv),
                    "cwd": command.cwd,
                    "env_overrides": dict(command.env),
                    "timeout_seconds": command.timeout_seconds,
                    "started_at": _utc(command_started),
                    "finished_at": _utc(command_finished),
                    "duration_ms": round(duration_ms, 3),
                    "exit_code": execution.exit_code,
                    "status": "failed",
                    "stdout_bytes": len(execution.stdout),
                    "stdout_sha256": _sha256_bytes(execution.stdout),
                    "stderr_bytes": len(execution.stderr),
                    "stderr_sha256": _sha256_bytes(execution.stderr),
                    "result_validation": {
                        "kind": command.validator,
                        "status": "failed",
                    },
                }
                payload["commands"].append(record)
                try:
                    summary, result_hash = _validate_command_result(
                        command,
                        execution,
                        root=self._root,
                        candidate_sha=candidate_sha,
                    )
                except EvidenceProducerError as exc:
                    payload["reason_codes"].append(str(exc))
                    break
                record["status"] = "passed"
                record["result_validation"] = {
                    "kind": command.validator,
                    "status": "passed",
                    "observed": summary,
                }
                if result_hash is not None:
                    path, digest = result_hash
                    payload["result_hashes"][path] = digest
            else:
                payload["status"] = "passed"
        except EvidenceBlocked as exc:
            payload["reason_codes"].append(exc.reason_code)

        if payload["status"] != "passed":
            payload["status"] = (
                "blocked"
                if not payload["commands"]
                else "failed"
            )
        payload["reason_codes"] = sorted(set(payload["reason_codes"]))
        payload["produced_at"] = _utc(self._clock())
        _atomic_write_json(output, payload)
        return payload


def run_producer(
    *,
    candidate_sha: str,
    suites: Sequence[str] = REQUIRED_SUITES,
    producer: EvidenceProducer | None = None,
) -> dict[str, Any]:
    if not suites or any(suite not in REQUIRED_SUITES for suite in suites):
        raise EvidenceProducerError("suite_selection_invalid")
    if len(set(suites)) != len(suites):
        raise EvidenceProducerError("suite_selection_duplicate")
    resolved = producer or EvidenceProducer()
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for suite in suites:
        try:
            evidence.append(
                resolved.produce_suite(
                    suite=suite,
                    candidate_sha=candidate_sha,
                )
            )
        except EvidenceProducerError as exc:
            errors.append({"suite": suite, "reason": str(exc)})
    statuses = [item.get("status") for item in evidence]
    status = (
        "passed"
        if not errors and len(evidence) == len(suites) and all(
            item == "passed" for item in statuses
        )
        else "blocked"
        if errors or any(item == "blocked" for item in statuses)
        else "failed"
    )
    return {
        "producer": PRODUCER_NAME,
        "candidate_sha": candidate_sha,
        "status": status,
        "suites": [
            {"suite": item["suite"], "status": item["status"]}
            for item in evidence
        ],
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument(
        "--suite",
        action="append",
        choices=REQUIRED_SUITES,
        dest="suites",
    )
    arguments = parser.parse_args()
    try:
        summary = run_producer(
            candidate_sha=arguments.candidate_sha,
            suites=tuple(arguments.suites or REQUIRED_SUITES),
        )
    except EvidenceProducerError as exc:
        summary = {
            "producer": PRODUCER_NAME,
            "candidate_sha": arguments.candidate_sha,
            "status": "blocked",
            "errors": [{"reason": str(exc)}],
        }
    print(json.dumps(summary, sort_keys=True))
    if summary["status"] == "passed":
        return 0
    return 2 if summary["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
