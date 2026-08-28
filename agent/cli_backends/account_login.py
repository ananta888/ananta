"""Worker-local browser login sessions for provisioned CLI backends."""

from __future__ import annotations

import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent.cli_backends.context import default_context as _ctx
from agent.cli_backends.provisioning import resolve_provisioned_backend_binary

SUPPORTED_ACCOUNT_LOGIN_BACKENDS = frozenset({"codex", "claude_code"})
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HTTPS_URL = re.compile(r"https://[^\s\x1b]+")
_DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b")
_SESSION_TTL_SECONDS = 15 * 60
_MAX_INPUT_LENGTH = 4096


class TerminalBridgePort(Protocol):
    """Minimal terminal process boundary required by account login."""

    process: Any

    def start(self) -> None: ...

    def wait_for_output(self, timeout: float) -> bool: ...

    def drain(self) -> list[str]: ...

    def write(self, value: str) -> None: ...

    def close(self) -> None: ...


BridgeFactory = Callable[..., TerminalBridgePort]
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class CliBackendAccountLoginError(RuntimeError):
    """A bounded account-login operation could not be completed."""


@dataclass
class _LoginSession:
    session_id: str
    backend: str
    bridge: TerminalBridgePort
    created_at: float
    expires_at: float
    output: str = ""
    terminal_status: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class CliBackendAccountLoginService:
    """Owns short-lived login processes inside one Worker container.

    The service executes only fixed commands for the pinned Codex and Claude
    binaries. Browser-facing callers receive the authorization URL/code but
    never Worker credential files.
    """

    def __init__(
        self,
        *,
        bridge_factory: BridgeFactory | None = None,
        binary_resolver: Callable[[str], str | None] = resolve_provisioned_backend_binary,
        run_command: RunCommand = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bridge_factory = bridge_factory or _ctx.terminal_bridge_factory
        self._binary_resolver = binary_resolver
        self._run_command = run_command
        self._clock = clock
        self._sessions: dict[str, _LoginSession] = {}
        self._lock = threading.RLock()

    def account_status(self, backend_id: str) -> dict:
        backend, binary = self._resolve_backend(backend_id)
        command = [binary, "login", "status"] if backend == "codex" else [binary, "auth", "status", "--json"]
        try:
            result = self._run_command(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CliBackendAccountLoginError("account_status_failed") from exc
        return {
            "backend": backend,
            "authenticated": result.returncode == 0,
            "status": "authenticated" if result.returncode == 0 else "not_authenticated",
        }

    def start(self, backend_id: str) -> dict:
        backend, binary = self._resolve_backend(backend_id)
        self._expire_sessions()
        command = [binary, "login", "--device-auth"] if backend == "codex" else [binary, "auth", "login", "--claudeai"]
        bridge = self._bridge_factory(binary, argv=command)
        try:
            bridge.start()
        except Exception as exc:
            bridge.close()
            raise CliBackendAccountLoginError("account_login_start_failed") from exc

        now = self._clock()
        session = _LoginSession(
            session_id=secrets.token_urlsafe(24),
            backend=backend,
            bridge=bridge,
            created_at=now,
            expires_at=now + _SESSION_TTL_SECONDS,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        bridge.wait_for_output(3.0)
        return self._snapshot(session)

    def status(self, backend_id: str, session_id: str) -> dict:
        return self._snapshot(self._session(backend_id, session_id))

    def submit_input(self, backend_id: str, session_id: str, value: str) -> dict:
        session = self._session(backend_id, session_id)
        safe_value = str(value or "").strip()
        if not safe_value or len(safe_value) > _MAX_INPUT_LENGTH:
            raise CliBackendAccountLoginError("invalid_account_login_input")
        with session.lock:
            if self._state(session) != "pending":
                raise CliBackendAccountLoginError("account_login_not_pending")
            session.bridge.write(f"{safe_value}\n")
            session.bridge.wait_for_output(2.0)
        return self._snapshot(session)

    def cancel(self, backend_id: str, session_id: str) -> dict:
        session = self._session(backend_id, session_id)
        with session.lock:
            session.terminal_status = "cancelled"
            session.bridge.close()
        return self._snapshot(session)

    def _resolve_backend(self, backend_id: str) -> tuple[str, str]:
        backend = str(backend_id or "").strip().lower()
        if backend not in SUPPORTED_ACCOUNT_LOGIN_BACKENDS:
            raise CliBackendAccountLoginError("account_login_backend_unsupported")
        binary = self._binary_resolver(backend)
        if not binary:
            raise CliBackendAccountLoginError("backend_not_installed")
        return backend, binary

    def _session(self, backend_id: str, session_id: str) -> _LoginSession:
        backend = str(backend_id or "").strip().lower()
        normalized_id = str(session_id or "").strip()
        with self._lock:
            session = self._sessions.get(normalized_id)
        if session is None or session.backend != backend:
            raise CliBackendAccountLoginError("account_login_session_not_found")
        return session

    def _expire_sessions(self) -> None:
        now = self._clock()
        with self._lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            if session.expires_at <= now and session.terminal_status is None:
                with session.lock:
                    session.terminal_status = "expired"
                    session.bridge.close()

    def _snapshot(self, session: _LoginSession) -> dict:
        with session.lock:
            chunks = session.bridge.drain()
            if chunks:
                session.output = (session.output + "".join(chunks))[-16000:]
            state = self._state(session)
            clean_output = _ANSI_ESCAPE.sub("", session.output)
            url_match = _HTTPS_URL.search(clean_output)
            code_match = _DEVICE_CODE.search(clean_output)
            requires_input = (
                session.backend == "claude_code" and "Paste code here" in clean_output and state == "pending"
            )
            return {
                "backend": session.backend,
                "session_id": session.session_id,
                "status": state,
                "authenticated": state == "authenticated",
                "verification_url": (url_match.group(0).rstrip(".,);") if url_match else None),
                "user_code": code_match.group(0) if code_match else None,
                "requires_input": requires_input,
                "expires_at": session.expires_at,
            }

    def _state(self, session: _LoginSession) -> str:
        if session.terminal_status:
            return session.terminal_status
        if session.expires_at <= self._clock():
            session.terminal_status = "expired"
            session.bridge.close()
            return session.terminal_status
        process = session.bridge.process
        return_code = process.poll() if process is not None else None
        if return_code is None:
            return "pending"
        session.terminal_status = "authenticated" if return_code == 0 else "failed"
        return session.terminal_status


_default_account_login_service: CliBackendAccountLoginService | None = None
_default_service_lock = threading.Lock()


def get_cli_backend_account_login_service() -> CliBackendAccountLoginService:
    global _default_account_login_service
    if _default_account_login_service is None:
        with _default_service_lock:
            if _default_account_login_service is None:
                _default_account_login_service = CliBackendAccountLoginService()
    return _default_account_login_service
