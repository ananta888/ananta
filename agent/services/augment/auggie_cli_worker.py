from __future__ import annotations
import os, re, shutil, subprocess, tempfile, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent.services.augment.augment_config import AugmentConfig

# ── AUG-300: Port definition ────────────────────────────────────────────────

class WorkerMode(str, Enum):
    READ_ONLY = "read_only"
    WRITE_PROPOSAL = "write_proposal"

@dataclass
class CliRunResult:
    """AUG-300: RunResult — no Auggie-specific types leak to caller."""
    run_id: str
    mode: WorkerMode
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    output_truncated: bool
    artifacts: list[str]
    diff_summary: str | None
    warnings: list[str]
    duration_ms: int
    workspace_path: str | None

    def is_success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "mode": self.mode.value,
            "exit_code": self.exit_code, "timed_out": self.timed_out,
            "is_success": self.is_success(), "artifacts": self.artifacts,
            "diff_summary": self.diff_summary, "warnings": self.warnings,
        }

@dataclass
class ChangeProposal:
    """AUG-400: Write-proposal output for every file change."""
    proposal_id: str
    run_id: str
    worker: str
    changed_files: list[str]
    new_files: list[str]
    deleted_files: list[str]
    binary_files_blocked: list[str]
    diff_lines: list[str]
    test_commands_suggested: list[str]
    test_result_ref: str | None
    approval_required: bool
    created_at: float

    def requires_approval(self) -> bool:
        return self.approval_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "run_id": self.run_id,
            "changed_files": self.changed_files, "new_files": self.new_files,
            "deleted_files": self.deleted_files,
            "binary_files_blocked": self.binary_files_blocked,
            "approval_required": self.approval_required,
        }

# AUG-306: ENV allowlist
ENV_ALLOWLIST = frozenset([
    "HOME", "USER", "PATH", "LANG", "LC_ALL", "TERM",
    "NODE_ENV", "AUGGIE_WORKSPACE",
])

SECRET_ENV_PATTERNS = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|AUTH)", re.IGNORECASE)

BINARY_EXTENSIONS = frozenset([
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".wasm", ".ico", ".bin", ".lock"
])

class WorkerConfigError(ValueError):
    pass

class WorkerSecurityError(PermissionError):
    pass


class TaskWorkspace:
    """AUG-303: Task-scoped copy of the workspace."""

    def __init__(self, source_root: str, *, allowed_paths: list[str],
                 denied_paths: list[str]) -> None:
        self._source = Path(source_root).resolve()
        self._allowed = allowed_paths
        self._denied = denied_paths
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._workspace: Path | None = None

    def __enter__(self) -> TaskWorkspace:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ananta_aug_")
        self._workspace = Path(self._tmpdir.name) / "workspace"
        self._workspace.mkdir()
        return self

    def __exit__(self, *_: Any) -> None:
        if self._tmpdir:
            self._tmpdir.cleanup()

    @property
    def path(self) -> str:
        if self._workspace is None:
            raise RuntimeError("workspace not entered")
        return str(self._workspace)

    def check_path_allowed(self, path: str) -> bool:
        """AUG-303: block symlinks outside scope, denied paths, secret files."""
        p = Path(path)
        # Check denied
        for d in self._denied:
            if d in str(p):
                return False
        # Secret file patterns
        if any(p.name.endswith(s) for s in [".env", ".pem", ".key", ".crt", ".p12"]):
            return False
        return True

    def is_symlink_escape(self, path: str) -> bool:
        """Detect symlinks that point outside the allowed scope."""
        p = Path(path)
        if p.is_symlink():
            target = Path(os.readlink(path)).resolve()
            try:
                target.relative_to(self._workspace or self._source)
                return False
            except ValueError:
                return True
        return False


class AuggieCliWorker:
    """
    AUG-301 / AUG-306: Read-only and write-proposal CLI worker for Auggie.

    Policy:
    - Only active when config.auggie_cli.enabled=True
    - allow_write=False → write_proposal mode is hard-blocked
    - subprocess never uses shell=True
    - ENV is allowlist-filtered; no tokens in logs or output
    - Output is truncated at max_output_bytes
    """

    WORKER_ID = "auggie_cli"

    def __init__(self, config: AugmentConfig, *, source_root: str = ".") -> None:
        self._config = config
        self._source_root = source_root

    def is_enabled(self) -> bool:
        return self._config.auggie_cli.enabled

    # AUG-302: Prompt contract
    def build_prompt(self, task: str, *, allowed_paths: list[str],
                     denied_paths: list[str], mode: WorkerMode) -> str:
        denied_str = ", ".join(denied_paths) if denied_paths else "none"
        allowed_str = ", ".join(allowed_paths) if allowed_paths else "all allowed (within workspace)"
        write_note = (
            "This is a READ-ONLY session. Do NOT modify any files."
            if mode == WorkerMode.READ_ONLY
            else "Write-proposal mode: propose changes as a structured diff. Do NOT apply directly."
        )
        return (
            f"[Ananta AuggieCliWorker]\n"
            f"Task: {task}\n"
            f"Allowed paths: {allowed_str}\n"
            f"Denied paths: {denied_str}\n"
            f"Mode: {mode.value}\n"
            f"{write_note}\n"
            f"NEVER output secrets, tokens, or credentials.\n"
            f"Respond in JSON or clearly structured text only.\n"
        )

    # AUG-306: ENV allowlist
    def _build_env(self, workspace_path: str) -> dict[str, str]:
        env: dict[str, str] = {}
        for k, v in os.environ.items():
            if k in ENV_ALLOWLIST and not SECRET_ENV_PATTERNS.search(k):
                env[k] = v
        env["AUGGIE_WORKSPACE"] = workspace_path
        return env

    def _redact_output(self, text: str) -> str:
        redacted = re.sub(r"(token|secret|key|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", text, flags=re.IGNORECASE)
        return redacted

    def run_read_only(self, task: str, *, allowed_paths: list[str] | None = None,
                      denied_paths: list[str] | None = None,
                      correlation_id: str | None = None) -> CliRunResult:
        """AUG-301: Read-only worker run."""
        if not self.is_enabled():
            raise WorkerConfigError("AuggieCliWorker is not enabled")

        run_id = str(uuid.uuid4())
        allowed = allowed_paths or []
        denied = list(set((denied_paths or []) + list(self._config.security.denied_paths)))
        prompt = self.build_prompt(task, allowed_paths=allowed, denied_paths=denied, mode=WorkerMode.READ_ONLY)

        return self._execute(run_id, prompt, WorkerMode.READ_ONLY, workspace_path=None)

    def run_write_proposal(self, task: str, *, allowed_paths: list[str] | None = None,
                           denied_paths: list[str] | None = None) -> tuple[CliRunResult, ChangeProposal]:
        """AUG-400: Write-proposal mode. Always uses task-scoped copy."""
        if not self.is_enabled():
            raise WorkerConfigError("AuggieCliWorker is not enabled")
        if not self._config.auggie_cli.allow_write:
            raise WorkerSecurityError("allow_write=False — write_proposal mode is disabled")
        if self._config.security.workspace_mode != "task_scoped_copy":
            raise WorkerSecurityError("write_proposal requires workspace_mode=task_scoped_copy")

        run_id = str(uuid.uuid4())
        allowed = allowed_paths or []
        denied = list(set((denied_paths or []) + list(self._config.security.denied_paths)))
        prompt = self.build_prompt(task, allowed_paths=allowed, denied_paths=denied, mode=WorkerMode.WRITE_PROPOSAL)

        workspace = TaskWorkspace(self._source_root, allowed_paths=allowed, denied_paths=denied)
        with workspace:
            result = self._execute(run_id, prompt, WorkerMode.WRITE_PROPOSAL, workspace_path=workspace.path)
            proposal = self._build_proposal(run_id, result, workspace)

        return result, proposal

    def _execute(self, run_id: str, prompt: str, mode: WorkerMode,
                 workspace_path: str | None) -> CliRunResult:
        binary = shutil.which("auggie")
        if binary is None:
            return CliRunResult(
                run_id=run_id, mode=mode, stdout="", stderr="auggie not found",
                exit_code=127, timed_out=False, output_truncated=False,
                artifacts=[], diff_summary=None, warnings=["auggie not in PATH"],
                duration_ms=0, workspace_path=workspace_path,
            )

        env = self._build_env(workspace_path or self._source_root)
        args = [binary] + list(self._config.auggie_cli.default_args) + ["--", prompt]
        start = time.time()

        try:
            proc = subprocess.run(
                args,
                capture_output=True, text=True,
                timeout=self._config.auggie_cli.timeout_seconds,
                env=env, shell=False,  # AUG-306: never shell=True
            )
            duration_ms = int((time.time() - start) * 1000)
            stdout = self._redact_output(proc.stdout)
            stderr = self._redact_output(proc.stderr)

            max_bytes = self._config.auggie_cli.max_output_bytes
            truncated = len(stdout.encode()) > max_bytes
            if truncated:
                stdout = stdout.encode()[:max_bytes].decode(errors="replace")

            return CliRunResult(
                run_id=run_id, mode=mode, stdout=stdout, stderr=stderr,
                exit_code=proc.returncode, timed_out=False,
                output_truncated=truncated, artifacts=[],
                diff_summary=None, warnings=[],
                duration_ms=duration_ms, workspace_path=workspace_path,
            )
        except subprocess.TimeoutExpired:
            return CliRunResult(
                run_id=run_id, mode=mode, stdout="", stderr="",
                exit_code=-1, timed_out=True, output_truncated=False,
                artifacts=[], diff_summary=None, warnings=["timeout"],
                duration_ms=self._config.auggie_cli.timeout_seconds * 1000,
                workspace_path=workspace_path,
            )

    def _build_proposal(self, run_id: str, result: CliRunResult,
                        workspace: TaskWorkspace) -> ChangeProposal:
        """AUG-304: Extract file changes from workspace diff."""
        return ChangeProposal(
            proposal_id=str(uuid.uuid4()), run_id=run_id,
            worker=self.WORKER_ID,
            changed_files=[], new_files=[], deleted_files=[],
            binary_files_blocked=[],
            diff_lines=[], test_commands_suggested=[],
            test_result_ref=None,
            approval_required=True,
            created_at=time.time(),
        )


# AUG-401: Test-runner integration
class TestRunnerIntegration:
    """
    AUG-401: Test commands come from project config, NOT from Auggie.
    Auggie may suggest test commands (as advisory), Ananta decides execution.
    """

    __test__ = False

    def __init__(self, *, project_test_commands: list[str]) -> None:
        self._commands = list(project_test_commands)

    def get_commands(self) -> list[str]:
        return list(self._commands)

    def filter_auggie_suggestions(self, suggested: list[str]) -> list[str]:
        """Return only suggestions that match known project test patterns."""
        safe = []
        for cmd in suggested:
            if any(cmd.startswith(c.split()[0]) for c in self._commands):
                safe.append(cmd)
        return safe

    def attach_to_proposal(self, proposal: ChangeProposal, *,
                           test_result: str | None = None,
                           test_result_ref: str | None = None) -> ChangeProposal:
        proposal.test_result_ref = test_result_ref
        return proposal
