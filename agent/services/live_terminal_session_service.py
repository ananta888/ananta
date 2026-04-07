from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import tempfile
import threading
import time

from agent.config import settings
from agent.services.terminal_bridge import build_terminal_bridge

LOGGER = logging.getLogger("agent.live_terminal_session")

_TERMINAL_EXECUTION_MODES = {"live_terminal", "interactive_terminal"}


def _safe_shell() -> str:
    if os.name == "nt":
        candidates = [
            settings.shell_path,
            os.environ.get("COMSPEC"),
            r"C:\Windows\System32\cmd.exe",
            "cmd.exe",
        ]
        for shell in candidates:
            if shell and (os.path.exists(shell) or "\\" not in shell):
                return shell
        return "cmd.exe"

    shell = settings.shell_path or "/bin/sh"
    return shell if os.path.exists(shell) else "/bin/sh"


def _normalize_terminal_execution_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in _TERMINAL_EXECUTION_MODES:
        return mode
    return "live_terminal"


def _normalize_interactive_launch_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"run", "tui"}:
        return mode
    return "run"


def _build_stdin_heredoc_command(command: str, prompt: str) -> tuple[str, str]:
    delimiter = f"__ANANTA_OPENCODE_PROMPT_{time.time_ns()}__"
    while delimiter in prompt:
        delimiter = f"{delimiter}_X"
    heredoc = f"{command} <<'{delimiter}'\n{prompt}\n{delimiter}"
    return heredoc, f"{command} <<'{delimiter}'"


class ManagedLiveTerminalSession:
    def __init__(self, session_id: str, *, shell: str | None = None) -> None:
        self.id = str(session_id or "").strip()
        self.shell = shell or _safe_shell()
        self.bridge = build_terminal_bridge(self.shell)
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._chunks: list[str] = []
        self._closed = False
        self._temp_dirs: list[tempfile.TemporaryDirectory[str]] = []
        self._runtime_cache: dict[str, object] = {}
        self._restart_spec: dict[str, object] | None = None
        self._workdir: str | None = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._reader: threading.Thread | None = None

    @property
    def transport(self) -> str:
        return "pty" if getattr(self.bridge, "is_pty", False) else "pipe"

    def start(self) -> None:
        process = getattr(self.bridge, "process", None)
        if process and process.poll() is None:
            return
        self._replace_bridge()

    def _replace_bridge(
        self,
        *,
        argv: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        reset_output: bool = False,
    ) -> None:
        try:
            self.bridge.close()
        except Exception:
            LOGGER.debug("Failed to close existing terminal bridge for %s", self.id, exc_info=True)
        self.bridge = build_terminal_bridge(self.shell, argv=argv, cwd=cwd, env=env)
        if reset_output:
            with self._condition:
                self._chunks = []
        self.bridge.start()
        self._closed = False
        self.updated_at = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _append_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        with self._condition:
            self._chunks.append(chunk)
            self.updated_at = time.time()
            self._condition.notify_all()

    def _read_loop(self) -> None:
        while True:
            process = getattr(self.bridge, "process", None)
            if process is None:
                break
            chunks = self.bridge.drain()
            if chunks:
                for chunk in chunks:
                    self._append_chunk(chunk)
            elif process.poll() is not None:
                break
            time.sleep(0.05)
        with self._condition:
            self._closed = True
            self.updated_at = time.time()
            self._condition.notify_all()

    def snapshot(self) -> str:
        with self._condition:
            return "".join(self._chunks)

    def read_from(self, offset: int) -> tuple[list[str], int]:
        with self._condition:
            start = max(0, min(int(offset or 0), len(self._chunks)))
            return list(self._chunks[start:]), len(self._chunks)

    def wait_for_update(self, offset: int, timeout: float) -> bool:
        deadline = time.time() + max(0.0, timeout)
        with self._condition:
            current = len(self._chunks)
            while not self._closed and len(self._chunks) <= max(0, offset) and time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return len(self._chunks) > current or self._closed

    def write(self, data: str) -> None:
        if not data:
            return
        self.start()
        with self._write_lock:
            self.bridge.write(data)
        self.updated_at = time.time()

    def resize(self, cols: int, rows: int) -> None:
        self.start()
        resize = getattr(self.bridge, "resize", None)
        if callable(resize):
            resize(cols, rows)
            self.updated_at = time.time()

    def _ensure_runtime_environment(self, runtime_cfg: dict[str, object]) -> dict[str, str]:
        provider_config = runtime_cfg.get("provider_config")
        if not provider_config:
            return {}
        cached = self._runtime_cache.get("provider_config")
        if cached == provider_config and isinstance(self._runtime_cache.get("env"), dict):
            return dict(self._runtime_cache["env"])
        temp_dir = tempfile.TemporaryDirectory()
        config_dir = os.path.join(temp_dir.name, "opencode")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(provider_config, handle, ensure_ascii=True)
        env = {
            "XDG_CONFIG_HOME": temp_dir.name,
            "OPENCODE_CONFIG_CONTENT": json.dumps(provider_config, ensure_ascii=True),
        }
        self._temp_dirs.append(temp_dir)
        self._runtime_cache = {"provider_config": provider_config, "env": dict(env)}
        return env

    def _build_wrapped_command(
        self,
        command: str,
        *,
        marker: str,
        suppress_input_echo: bool,
    ) -> str:
        marker_cmd = f"printf '\\n{marker} %s\\n' $?\n"
        _ = suppress_input_echo
        return f"{command}\n{marker_cmd}"

    def _build_cd_command(self, workdir: str) -> str:
        normalized = os.path.abspath(str(workdir))
        if os.name == "nt":
            escaped = normalized.replace('"', '""')
            return f'cd /d "{escaped}"'
        return f"cd {shlex.quote(normalized)}"

    def run_command(
        self,
        command: str,
        *,
        timeout: int,
        visible_command: str | None = None,
        suppress_input_echo: bool = False,
    ) -> tuple[int, str, str]:
        self.start()
        marker = f"__ANANTA_TERM_RC__{time.time_ns()}__"
        marker_pattern = re.compile(rf"(?:\r?\n)?{re.escape(marker)} (?P<rc>-?\d+)(?:\r?\n)?")
        with self._command_lock:
            self._restart_spec = {
                "kind": "shell_command",
                "command": str(command),
                "visible_command": str(visible_command or ""),
                "suppress_input_echo": bool(suppress_input_echo),
            }
            if visible_command:
                self._append_chunk(f"$ {visible_command}\n")
            capture_from = len(self._chunks)
            deadline = time.time() + max(1, int(timeout or 60))
            cursor = capture_from
            collected: list[str] = []
            echo_suppressed = bool(
                suppress_input_echo and getattr(self.bridge, "is_pty", False) and os.name != "nt"
            )
            try:
                if echo_suppressed:
                    self.bridge.set_echo(False)
                self.write(self._build_wrapped_command(command, marker=marker, suppress_input_echo=suppress_input_echo))
                while time.time() < deadline:
                    chunks, cursor = self.read_from(cursor)
                    if chunks:
                        collected.extend(chunks)
                        text = "".join(collected)
                        match = marker_pattern.search(text)
                        if match:
                            output = text[: match.start()].strip()
                            return int(match.group("rc")), output, ""
                    self.wait_for_update(cursor, min(0.25, max(0.05, deadline - time.time())))
                return -1, "".join(collected).strip(), "Timeout"
            finally:
                if echo_suppressed:
                    self.bridge.set_echo(True)

    def run_foreground_command(
        self,
        argv: list[str],
        *,
        timeout: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        reset_output: bool = True,
    ) -> tuple[int, str, str]:
        if not argv:
            return -1, "", "missing_command"
        with self._command_lock:
            self._restart_spec = {
                "kind": "foreground",
                "argv": list(argv),
                "cwd": str(cwd) if cwd else None,
                "env": dict(env or {}),
                "reset_output": bool(reset_output),
            }
            capture_from = 0 if reset_output else len(self._chunks)
            self._replace_bridge(argv=argv, cwd=cwd, env=env, reset_output=reset_output)
            cursor = capture_from
            collected: list[str] = []
            deadline = time.time() + max(1, int(timeout or 60))
            while time.time() < deadline:
                chunks, cursor = self.read_from(cursor)
                if chunks:
                    collected.extend(chunks)
                process = getattr(self.bridge, "process", None)
                if process is not None and process.poll() is not None:
                    time.sleep(0.05)
                    trailing, cursor = self.read_from(cursor)
                    if trailing:
                        collected.extend(trailing)
                    return int(process.returncode or 0), "".join(collected).strip(), ""
                self.wait_for_update(cursor, min(0.25, max(0.05, deadline - time.time())))
            self.bridge.close()
            return -1, "".join(collected).strip(), "Timeout"

    def restart(self) -> dict[str, object]:
        spec = dict(self._restart_spec or {})
        if not spec:
            self._replace_bridge(reset_output=True)
            return {"ok": True, "restart_kind": "shell", "restarted": True, "fallback": True}

        kind = str(spec.get("kind") or "").strip().lower()
        with self._command_lock:
            if kind == "foreground":
                self._replace_bridge(
                    argv=list(spec.get("argv") or []),
                    cwd=str(spec.get("cwd") or "") or None,
                    env=dict(spec.get("env") or {}),
                    reset_output=bool(spec.get("reset_output", True)),
                )
                return {"ok": True, "restart_kind": "foreground", "restarted": True}

            self._replace_bridge(reset_output=True)
            if self._workdir:
                self.write(f"{self._build_cd_command(self._workdir)}\n")
            visible_command = str(spec.get("visible_command") or "").strip()
            if visible_command:
                self._append_chunk(f"$ {visible_command}\n")
            command = str(spec.get("command") or "").strip()
            if command:
                self.write(f"{command}\n")
            return {"ok": True, "restart_kind": "shell_command", "restarted": True}

    def ensure_workdir(self, workdir: str | None) -> None:
        normalized = os.path.abspath(str(workdir or "")).strip() if workdir else ""
        if not normalized or normalized == self._workdir:
            return
        rc, _, err = self.run_command(self._build_cd_command(normalized), timeout=10)
        if rc != 0:
            LOGGER.warning("Failed to switch live terminal %s to workdir %s: %s", self.id, normalized, err or rc)
            return
        self._workdir = normalized

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self.updated_at = time.time()
            self._condition.notify_all()
        self.bridge.close()
        for temp_dir in self._temp_dirs:
            try:
                temp_dir.cleanup()
            except Exception:
                LOGGER.debug("Failed to cleanup temp dir for %s", self.id, exc_info=True)
        self._temp_dirs = []

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed


class LiveTerminalSessionService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, ManagedLiveTerminalSession] = {}

    def ensure_session(self, session_id: str, *, start: bool = True) -> ManagedLiveTerminalSession:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("missing_session_id")
        with self._lock:
            session = self._sessions.get(normalized)
            if session is None:
                session = ManagedLiveTerminalSession(normalized)
                self._sessions[normalized] = session
            elif session.closed and start:
                session = ManagedLiveTerminalSession(normalized)
                self._sessions[normalized] = session
        if start:
            session.start()
        return session

    def _get_managed_session(self, session_id: str) -> ManagedLiveTerminalSession | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            return self._sessions.get(normalized)

    def get_session(self, session_id: str) -> dict | None:
        session = self._get_managed_session(session_id)
        if session is None:
            return None
        return {
            "id": session.id,
            "forward_param": session.id,
            "shell": session.shell,
            "transport": session.transport,
            "status": "closed" if session.closed else "active",
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def read_from(self, session_id: str, offset: int) -> tuple[list[str], int]:
        session = self.ensure_session(session_id, start=False)
        return session.read_from(offset)

    def wait_for_update(self, session_id: str, offset: int, timeout: float) -> bool:
        session = self.ensure_session(session_id, start=False)
        return session.wait_for_update(offset, timeout)

    def write(self, session_id: str, data: str) -> None:
        session = self.ensure_session(session_id)
        session.write(data)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self.ensure_session(session_id)
        session.resize(cols, rows)

    def restart(self, session_id: str) -> dict[str, object]:
        session = self._get_managed_session(session_id)
        if session is None:
            return {"ok": False, "message": "terminal_session_not_found", "restarted": False}
        result = session.restart()
        return {"ok": bool(result.get("ok")), **result, "session_id": session.id}

    def append_output(self, session_id: str, data: str) -> None:
        if not data:
            return
        session = self.ensure_session(session_id)
        session._append_chunk(str(data))

    def ensure_session_for_cli(
        self,
        cli_session: dict | None,
        *,
        execution_mode: str | None = None,
        workdir: str | None = None,
    ) -> dict | None:
        if not isinstance(cli_session, dict):
            return None
        session_id = str(cli_session.get("id") or "").strip()
        if not session_id:
            return None
        metadata = cli_session.get("metadata") if isinstance(cli_session.get("metadata"), dict) else {}
        mode = _normalize_terminal_execution_mode(
            execution_mode
            or (metadata.get("opencode_execution_mode") if isinstance(metadata, dict) else None)
        )
        launch_mode = _normalize_interactive_launch_mode(
            metadata.get("opencode_interactive_launch_mode") if isinstance(metadata, dict) else None
        )
        session = self.ensure_session(session_id, start=(mode != "interactive_terminal"))
        session_workdir = workdir or (metadata.get("opencode_workdir") if isinstance(metadata, dict) else None)
        if session_workdir:
            normalized_workdir = os.path.abspath(str(session_workdir))
            if mode == "interactive_terminal":
                session._workdir = normalized_workdir
            else:
                session.ensure_workdir(normalized_workdir)
        return {
            "terminal_session_id": session.id,
            "forward_param": session.id,
            "agent_url": settings.agent_url,
            "agent_name": settings.agent_name,
            "shell": session.shell,
            "transport": session.transport,
            "execution_mode": mode,
            "interactive_launch_mode": launch_mode,
            "workdir": session._workdir,
            "status": "closed" if session.closed else "active",
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def run_opencode_turn(
        self,
        cli_session: dict,
        *,
        prompt: str,
        timeout: int = 60,
        model: str | None = None,
        workdir: str | None = None,
    ) -> tuple[int, str, str]:
        from agent.common.sgpt import resolve_opencode_runtime_config

        opencode_bin = settings.opencode_path or "opencode"
        opencode_resolved = shutil.which(opencode_bin)
        if opencode_resolved is None:
            return -1, "", f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai"

        session_meta = self.ensure_session_for_cli(cli_session, workdir=workdir) or {}
        if not session_meta:
            return -1, "", "live_terminal_session_missing"
        mode = _normalize_terminal_execution_mode(str(session_meta.get("execution_mode") or "live_terminal"))
        interactive_launch_mode = _normalize_interactive_launch_mode(
            str(session_meta.get("interactive_launch_mode") or "run")
        )
        session = self.ensure_session(
            str(session_meta.get("terminal_session_id") or ""),
            start=(mode != "interactive_terminal"),
        )
        runtime_cfg = resolve_opencode_runtime_config(model=model)
        env = session._ensure_runtime_environment(runtime_cfg)
        normalized_workdir = str(session_meta.get("workdir") or "").strip()
        if not normalized_workdir and workdir:
            normalized_workdir = os.path.abspath(str(workdir))

        selected_model = str(runtime_cfg.get("model") or "").strip()
        if mode == "interactive_terminal" and interactive_launch_mode == "tui":
            args = [opencode_resolved]
            if normalized_workdir:
                args.append(normalized_workdir)
            if selected_model:
                args.extend(["--model", selected_model])
            if prompt:
                args.extend(["--prompt", str(prompt)])
            return session.run_foreground_command(
                args,
                timeout=timeout,
                cwd=normalized_workdir or None,
                env=env,
            )

        args = [opencode_resolved, "run"]
        selected_model = str(runtime_cfg.get("model") or "").strip()
        if selected_model:
            args.extend(["--model", selected_model])
        if normalized_workdir:
            args.extend(["--dir", normalized_workdir])

        visible = " ".join(shlex.quote(part) for part in args)
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in env.items() if value and key in {"XDG_CONFIG_HOME"}
        )
        base_command = visible if not env_prefix else f"{env_prefix} {visible}"
        cmd, visible_command = _build_stdin_heredoc_command(base_command, str(prompt or ""))
        return session.run_command(
            cmd,
            timeout=timeout,
            visible_command=visible_command,
            suppress_input_echo=(mode == "interactive_terminal"),
        )

    def close_session(self, session_id: str) -> None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return
        with self._lock:
            session = self._sessions.pop(normalized, None)
        if session is not None:
            session.close()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            sessions = list(self._sessions.values())
        active = [session for session in sessions if not session.closed]
        return {
            "total": len(sessions),
            "active": len(active),
            "items": [
                {
                    "id": session.id,
                    "shell": session.shell,
                    "transport": session.transport,
                    "status": "closed" if session.closed else "active",
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                }
                for session in sessions
            ],
        }


live_terminal_session_service = LiveTerminalSessionService()


def get_live_terminal_session_service() -> LiveTerminalSessionService:
    return live_terminal_session_service
