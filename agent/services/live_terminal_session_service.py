from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

from agent.config import settings

LOGGER = logging.getLogger("agent.live_terminal_session")


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


class ManagedLiveTerminalSession:
    def __init__(self, session_id: str, *, shell: str | None = None) -> None:
        self.id = str(session_id or "").strip()
        self.shell = shell or _safe_shell()
        self.process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._chunks: list[str] = []
        self._closed = False
        self._temp_dirs: list[tempfile.TemporaryDirectory[str]] = []
        self._runtime_cache: dict[str, object] = {}
        self.created_at = time.time()
        self.updated_at = self.created_at

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(  # noqa: S603
            [self.shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=(os.name != "nt"),
            creationflags=creationflags,
        )
        self._closed = False
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
        if not self.process or not self.process.stdout:
            return
        while True:
            try:
                data = self.process.stdout.read(4096)
            except Exception:
                break
            if not data:
                break
            self._append_chunk(data.decode("utf-8", errors="ignore"))
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
        if not self.process or not self.process.stdin:
            raise RuntimeError("terminal_session_unavailable")
        with self._write_lock:
            self.process.stdin.write(data.encode("utf-8", errors="ignore"))
            self.process.stdin.flush()
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

    def run_command(self, command: str, *, timeout: int, visible_command: str | None = None) -> tuple[int, str, str]:
        self.start()
        marker = f"__ANANTA_TERM_RC__{time.time_ns()}__"
        marker_pattern = re.compile(rf"(?:\r?\n)?{re.escape(marker)} (?P<rc>-?\d+)(?:\r?\n)?")
        with self._command_lock:
            if visible_command:
                self._append_chunk(f"$ {visible_command}\n")
            capture_from = len(self._chunks)
            wrapped = f"{command}\nprintf '\\n{marker} %s\\n' $?\n"
            self.write(wrapped)
            deadline = time.time() + max(1, int(timeout or 60))
            cursor = capture_from
            collected: list[str] = []
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

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self.updated_at = time.time()
            self._condition.notify_all()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                LOGGER.debug("Failed to terminate terminal session %s", self.id, exc_info=True)
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

    def ensure_session(self, session_id: str) -> ManagedLiveTerminalSession:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("missing_session_id")
        with self._lock:
            session = self._sessions.get(normalized)
            if session is None or session.closed:
                session = ManagedLiveTerminalSession(normalized)
                session.start()
                self._sessions[normalized] = session
        return session

    def get_session(self, session_id: str) -> dict | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            session = self._sessions.get(normalized)
        if session is None:
            return None
        return {
            "id": session.id,
            "forward_param": session.id,
            "shell": session.shell,
            "status": "closed" if session.closed else "active",
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def read_from(self, session_id: str, offset: int) -> tuple[list[str], int]:
        session = self.ensure_session(session_id)
        return session.read_from(offset)

    def wait_for_update(self, session_id: str, offset: int, timeout: float) -> bool:
        session = self.ensure_session(session_id)
        return session.wait_for_update(offset, timeout)

    def write(self, session_id: str, data: str) -> None:
        session = self.ensure_session(session_id)
        session.write(data)

    def ensure_session_for_cli(self, cli_session: dict | None) -> dict | None:
        if not isinstance(cli_session, dict):
            return None
        session_id = str(cli_session.get("id") or "").strip()
        if not session_id:
            return None
        session = self.ensure_session(session_id)
        return {
            "terminal_session_id": session.id,
            "forward_param": session.id,
            "shell": session.shell,
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

        session_meta = self.ensure_session_for_cli(cli_session)
        if not session_meta:
            return -1, "", "live_terminal_session_missing"
        session = self.ensure_session(str(session_meta.get("terminal_session_id") or ""))
        runtime_cfg = resolve_opencode_runtime_config(model=model)
        env = session._ensure_runtime_environment(runtime_cfg)
        args = [opencode_resolved, "run"]
        selected_model = str(runtime_cfg.get("model") or "").strip()
        if selected_model:
            args.extend(["--model", selected_model])
        args.append(str(prompt or ""))
        visible = " ".join(shlex.quote(part) for part in args)
        env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items() if value)
        cmd = visible if not env_prefix else f"{env_prefix} {visible}"
        if workdir:
            cmd = f"cd {shlex.quote(workdir)} && {cmd}"
        return session.run_command(cmd, timeout=timeout, visible_command=cmd)

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
