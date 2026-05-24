from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass


_SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]+$")


@dataclass(frozen=True)
class TmuxBackendSession:
    tmux_session_name: str
    pane_target: str


class TmuxBackendError(RuntimeError):
    pass


class TmuxSessionBackend:
    def _require_tmux(self) -> str:
        tmux_path = shutil.which("tmux")
        if not tmux_path:
            raise TmuxBackendError("tmux_binary_missing")
        return tmux_path

    def _normalize_name(self, unsafe_hint: str | None = None) -> str:
        base = str(unsafe_hint or "").strip().lower()
        if base:
            base = re.sub(r"[^a-z0-9._:-]+", "-", base)
            base = re.sub(r"-{2,}", "-", base)
            base = base.strip("-._:")
        if not base:
            base = "ananta-terminal"
        return f"{base}-{uuid.uuid4().hex[:10]}"

    def _validate_name(self, value: str) -> str:
        name = str(value or "").strip()
        if not name or not _SAFE_NAME_PATTERN.fullmatch(name):
            raise TmuxBackendError("tmux_session_name_invalid")
        return name

    def create_session(self, *, name_hint: str | None = None, cwd: str | None = None) -> TmuxBackendSession:
        tmux = self._require_tmux()
        session_name = self._validate_name(self._normalize_name(name_hint))
        command = [tmux, "new-session", "-d", "-s", session_name]
        if cwd:
            command.extend(["-c", str(cwd)])
        result = subprocess.run(command, capture_output=True, text=True)  # noqa: S603
        if result.returncode != 0:
            raise TmuxBackendError("tmux_create_failed")
        return TmuxBackendSession(tmux_session_name=session_name, pane_target=f"{session_name}:0.0")

    def send_input(self, *, session_name: str, text: str) -> None:
        tmux = self._require_tmux()
        name = self._validate_name(session_name)
        result = subprocess.run([tmux, "send-keys", "-t", f"{name}:0.0", str(text), "C-m"], capture_output=True, text=True)  # noqa: S603
        if result.returncode != 0:
            raise TmuxBackendError("tmux_send_input_failed")

    def capture_output(self, *, session_name: str, lines: int = 200) -> str:
        tmux = self._require_tmux()
        name = self._validate_name(session_name)
        safe_lines = max(1, min(int(lines or 200), 5000))
        result = subprocess.run(
            [tmux, "capture-pane", "-p", "-S", f"-{safe_lines}", "-t", f"{name}:0.0"],
            capture_output=True,
            text=True,
        )  # noqa: S603
        if result.returncode != 0:
            raise TmuxBackendError("tmux_capture_failed")
        return str(result.stdout or "")

    def kill_session(self, *, session_name: str) -> None:
        tmux = self._require_tmux()
        name = self._validate_name(session_name)
        result = subprocess.run([tmux, "kill-session", "-t", name], capture_output=True, text=True)  # noqa: S603
        if result.returncode != 0:
            raise TmuxBackendError("tmux_kill_failed")


_SERVICE = TmuxSessionBackend()


def get_tmux_session_backend() -> TmuxSessionBackend:
    return _SERVICE
