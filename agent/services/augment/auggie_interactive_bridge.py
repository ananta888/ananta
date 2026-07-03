from __future__ import annotations
import os, re, shutil, subprocess, sys, time, threading, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agent.services.augment.augment_config import AugmentConfig
from agent.services.augment.auggie_cli_worker import (
    ChangeProposal, ENV_ALLOWLIST, SECRET_ENV_PATTERNS
)

class SessionStatus(str, Enum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    IDLE_TIMEOUT = "idle_timeout"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"

class TurnKind(str, Enum):
    USER_INPUT = "user_input"
    AUGGIE_OUTPUT = "auggie_output"
    SYSTEM_EVENT = "system_event"
    APPROVAL_GATE = "approval_gate"

SECRET_REDACT_PATTERN = re.compile(
    r"(token|secret|key|password|bearer|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE
)

@dataclass
class SessionScope:
    workspace_id: str
    owner_id: str
    allowed_paths: list[str]
    denied_paths: list[str]
    mode: str  # "read_only" | "write_proposal"
    max_session_seconds: int = 1800
    idle_timeout_seconds: int = 120

@dataclass
class SessionTurn:
    turn_id: str
    kind: TurnKind
    content_redacted: str   # redacted content only — no raw secrets
    timestamp: float
    approval_required: bool = False
    approval_ref: str | None = None

@dataclass
class SessionRecord:
    session_id: str
    correlation_id: str
    scope: SessionScope
    status: SessionStatus
    turns: list[SessionTurn]
    change_proposal_ref: str | None
    started_at: float
    ended_at: float | None
    duration_ms: int | None
    error: str | None

    def add_turn(self, kind: TurnKind, content: str, *, approval_required: bool = False) -> SessionTurn:
        turn = SessionTurn(
            turn_id=str(uuid.uuid4())[:8],
            kind=kind,
            content_redacted=self._redact(content),
            timestamp=time.time(),
            approval_required=approval_required,
        )
        self.turns.append(turn)
        return turn

    def _redact(self, text: str) -> str:
        return SECRET_REDACT_PATTERN.sub(r"\1=[REDACTED]", text)

    def transcript_summary(self) -> str:
        lines = [f"Session {self.session_id} ({self.status.value})"]
        for t in self.turns:
            lines.append(f"  [{t.kind.value}] {t.content_redacted[:80]}")
        return "\n".join(lines)

    def tracking_info(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value,
            "owner": self.scope.owner_id,
            "workspace": self.scope.workspace_id,
            "scope_paths": self.scope.allowed_paths,
            "mode": self.scope.mode,
            "turn_count": len(self.turns),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "change_proposal_ref": self.change_proposal_ref,
        }

class SessionConfigError(ValueError):
    pass

class SessionSecurityError(PermissionError):
    pass

# AUG-501: PTY abstraction — isolated in this module
class _PtyProcess:
    """
    Thin wrapper around a subprocess to simulate PTY interaction.

    Platform notes:
    - Linux/macOS: pty.openpty() for true PTY support
    - Windows/WSL: falls back to subprocess pipes (no PTY)
    - Interactive mode detection via sys.platform
    """

    def __init__(self, args: list[str], *, env: dict[str, str], cwd: str) -> None:
        self._args = args
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._last_activity = time.time()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self._env, cwd=self._cwd,
            shell=False,  # NEVER shell=True
        )
        self._last_activity = time.time()

    def send(self, text: str) -> None:
        if self._proc and self._proc.stdin and not self._proc.stdin.closed:
            self._proc.stdin.write((text + "\n").encode())
            self._proc.stdin.flush()
            self._last_activity = time.time()

    def read_output(self, timeout: float = 2.0) -> str:
        if not self._proc:
            return ""
        try:
            out, _ = self._proc.communicate(timeout=timeout)
            return out.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return ""
        except Exception:
            return ""

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def idle_seconds(self) -> float:
        return time.time() - self._last_activity

    def terminate(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


class AuggieInteractiveBridge:
    """
    AUG-500-503: Managed interactive session with Auggie CLI.

    Policy:
    - No raw terminal objects leak to hub
    - Session has mandatory scope, owner, correlation-id, and idle timeout
    - Transcript is redacted before storage
    - write_proposal mode requires task-scoped copy
    - All file changes → ChangeProposal (requires approval)
    """

    WORKER_ID = "auggie_interactive"

    def __init__(self, config: AugmentConfig) -> None:
        self._config = config
        self._sessions: dict[str, SessionRecord] = {}

    def is_enabled(self) -> bool:
        return self._config.interactive_bridge.enabled

    def start_session(self, *, scope: SessionScope, correlation_id: str | None = None) -> SessionRecord:
        if not self.is_enabled():
            raise SessionConfigError("interactive_bridge is not enabled")
        if not self._config.interactive_bridge.approval_required_for_write:
            raise SessionSecurityError("approval_required_for_write must be True")
        if scope.mode == "write_proposal" and self._config.security.workspace_mode != "task_scoped_copy":
            raise SessionSecurityError("write_proposal requires task_scoped_copy workspace_mode")

        session_id = str(uuid.uuid4())
        record = SessionRecord(
            session_id=session_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            scope=scope,
            status=SessionStatus.INITIALIZING,
            turns=[],
            change_proposal_ref=None,
            started_at=time.time(),
            ended_at=None,
            duration_ms=None,
            error=None,
        )
        record.add_turn(TurnKind.SYSTEM_EVENT, f"Session started: owner={scope.owner_id} mode={scope.mode}")
        record.status = SessionStatus.RUNNING
        self._sessions[session_id] = record
        return record

    def send_turn(self, session_id: str, user_input: str) -> SessionTurn:
        record = self._get_session(session_id)
        self._assert_running(record)
        record.add_turn(TurnKind.USER_INPUT, user_input)
        # Simulate Auggie response (real impl would call _PtyProcess.send + read)
        response_turn = record.add_turn(TurnKind.AUGGIE_OUTPUT, "[auggie response — not yet connected to real PTY]")
        return response_turn

    def end_session(self, session_id: str, *, reason: str = "completed") -> tuple[SessionRecord, ChangeProposal | None]:
        record = self._get_session(session_id)
        record.add_turn(TurnKind.SYSTEM_EVENT, f"Session ended: {reason}")
        record.status = SessionStatus.COMPLETED
        record.ended_at = time.time()
        record.duration_ms = int((record.ended_at - record.started_at) * 1000)

        # AUG-502: Build ChangeProposal for write sessions
        proposal = None
        if record.scope.mode == "write_proposal":
            proposal = self._build_proposal(record)
            record.change_proposal_ref = proposal.proposal_id

        return record, proposal

    def kill_session(self, session_id: str, *, reason: str = "killed") -> SessionRecord:
        record = self._get_session(session_id)
        record.add_turn(TurnKind.SYSTEM_EVENT, f"Session killed: {reason}")
        record.status = SessionStatus.KILLED
        record.ended_at = time.time()
        record.duration_ms = int((record.ended_at - record.started_at) * 1000)
        return record

    def check_idle_timeout(self, session_id: str) -> bool:
        """AUG-501: Check and enforce idle timeout. Returns True if timed out."""
        record = self._sessions.get(session_id)
        if record is None or record.status != SessionStatus.RUNNING:
            return False
        last_turn_time = record.turns[-1].timestamp if record.turns else record.started_at
        idle_sec = time.time() - last_turn_time
        if idle_sec >= self._config.interactive_bridge.idle_timeout_seconds:
            record.add_turn(TurnKind.SYSTEM_EVENT, f"Idle timeout after {idle_sec:.0f}s")
            record.status = SessionStatus.IDLE_TIMEOUT
            record.ended_at = time.time()
            record.duration_ms = int((record.ended_at - record.started_at) * 1000)
            return True
        return False

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def get_tracking_info(self, session_id: str) -> dict[str, Any] | None:
        record = self._sessions.get(session_id)
        return record.tracking_info() if record else None

    def get_redacted_transcript(self, session_id: str) -> list[dict[str, Any]] | None:
        record = self._sessions.get(session_id)
        if record is None:
            return None
        return [
            {"turn_id": t.turn_id, "kind": t.kind.value,
             "content": t.content_redacted, "timestamp": t.timestamp}
            for t in record.turns
        ]

    def _get_session(self, session_id: str) -> SessionRecord:
        r = self._sessions.get(session_id)
        if r is None:
            raise KeyError(f"Unknown session: {session_id}")
        return r

    def _assert_running(self, record: SessionRecord) -> None:
        if record.status != SessionStatus.RUNNING:
            raise SessionConfigError(f"Session {record.session_id} is {record.status.value}, not running")

    def _build_proposal(self, record: SessionRecord) -> ChangeProposal:
        return ChangeProposal(
            proposal_id=str(uuid.uuid4()),
            run_id=record.session_id,
            worker=self.WORKER_ID,
            changed_files=[], new_files=[], deleted_files=[],
            binary_files_blocked=[],
            diff_lines=[],
            test_commands_suggested=[],
            test_result_ref=None,
            approval_required=True,
            created_at=time.time(),
        )

    def _build_env(self, workspace: str) -> dict[str, str]:
        env: dict[str, str] = {}
        for k, v in os.environ.items():
            if k in ENV_ALLOWLIST and not SECRET_ENV_PATTERNS.search(k):
                env[k] = v
        env["AUGGIE_WORKSPACE"] = workspace
        return env
