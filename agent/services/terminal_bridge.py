from __future__ import annotations

import os
import queue
import subprocess
import threading
from dataclasses import dataclass
import struct
import time

try:
    import fcntl
except Exception:  # pragma: no cover - unavailable on Windows
    fcntl = None  # type: ignore[assignment]

try:
    import termios
except Exception:  # pragma: no cover - unavailable on Windows
    termios = None  # type: ignore[assignment]


def _write_all(fd: int, payload: bytes, *, writer=os.write) -> None:
    total_written = 0
    while total_written < len(payload):
        written = writer(fd, payload[total_written:])
        if written <= 0:
            raise OSError("pty_write_failed")
        total_written += written


@dataclass
class PtyBridge:
    shell: str
    argv: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue(maxsize=4096)
        self._output_condition = threading.Condition()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    @property
    def is_pty(self) -> bool:
        return True

    def start(self) -> None:
        try:
            import pty  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError("pty_unavailable") from exc

        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        command = list(self.argv or [self.shell])
        env = ({**os.environ, **self.env} if self.env else None)
        self.process = subprocess.Popen(  # noqa: S603
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
            cwd=self.cwd or None,
            env=env,
        )
        os.close(slave_fd)

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def set_echo(self, enabled: bool) -> None:
        if self.master_fd is None or termios is None:
            return
        try:
            attrs = termios.tcgetattr(self.master_fd)
        except Exception:
            return
        if enabled:
            attrs[3] |= termios.ECHO | termios.ECHONL
        else:
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        try:
            termios.tcsetattr(self.master_fd, termios.TCSANOW, attrs)
        except Exception:
            return

    def _read_loop(self) -> None:
        if self.master_fd is None:
            return
        while not self._stop.is_set():
            try:
                chunk = os.read(self.master_fd, 4096)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="ignore")
                try:
                    self.output_queue.put_nowait(decoded)
                except queue.Full:
                    _ = self.output_queue.get_nowait()
                    self.output_queue.put_nowait(decoded)
                with self._output_condition:
                    self._output_condition.notify_all()
            except OSError:
                break
        with self._output_condition:
            self._output_condition.notify_all()

    def write(self, data: str) -> None:
        if self.master_fd is None:
            return
        _write_all(self.master_fd, data.encode("utf-8", errors="ignore"))

    def resize(self, cols: int, rows: int) -> None:
        if self.master_fd is None or termios is None or fcntl is None:
            return
        safe_cols = max(1, int(cols or 0))
        safe_rows = max(1, int(rows or 0))
        if not hasattr(termios, "TIOCSWINSZ"):
            return
        try:
            winsize = struct.pack("HHHH", safe_rows, safe_cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            return

    def drain(self) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    def wait_for_output(self, timeout: float) -> bool:
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        with self._output_condition:
            while self.output_queue.empty():
                process = self.process
                if self._stop.is_set() or process is None or process.poll() is not None:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._output_condition.wait(timeout=remaining)
            return not self.output_queue.empty()

    def close(self) -> None:
        self._stop.set()
        with self._output_condition:
            self._output_condition.notify_all()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


@dataclass
class PipeBridge:
    shell: str
    argv: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue(maxsize=4096)
        self._output_condition = threading.Condition()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    @property
    def is_pty(self) -> bool:
        return False

    def start(self) -> None:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = list(self.argv or [self.shell])
        env = ({**os.environ, **self.env} if self.env else None)
        self.process = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=(os.name != "nt"),
            creationflags=creationflags,
            cwd=self.cwd or None,
            env=env,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def set_echo(self, enabled: bool) -> None:
        return

    def _read_loop(self) -> None:
        if not self.process or not self.process.stdout:
            return
        while not self._stop.is_set():
            try:
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="ignore")
                try:
                    self.output_queue.put_nowait(decoded)
                except queue.Full:
                    _ = self.output_queue.get_nowait()
                    self.output_queue.put_nowait(decoded)
                with self._output_condition:
                    self._output_condition.notify_all()
            except Exception:
                break
        with self._output_condition:
            self._output_condition.notify_all()

    def write(self, data: str) -> None:
        if not self.process or not self.process.stdin:
            return
        try:
            self.process.stdin.write(data.encode("utf-8", errors="ignore"))
            self.process.stdin.flush()
        except Exception:
            pass

    def resize(self, cols: int, rows: int) -> None:
        _ = (cols, rows)
        return

    def drain(self) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    def wait_for_output(self, timeout: float) -> bool:
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        with self._output_condition:
            while self.output_queue.empty():
                process = self.process
                if self._stop.is_set() or process is None or process.poll() is not None:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._output_condition.wait(timeout=remaining)
            return not self.output_queue.empty()

    def close(self) -> None:
        self._stop.set()
        with self._output_condition:
            self._output_condition.notify_all()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass


def build_terminal_bridge(
    shell: str,
    *,
    argv: list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> PtyBridge | PipeBridge:
    if os.name == "nt":
        return PipeBridge(shell=shell, argv=argv, cwd=cwd, env=env)
    try:
        import pty  # noqa: F401
    except Exception:
        return PipeBridge(shell=shell, argv=argv, cwd=cwd, env=env)
    return PtyBridge(shell=shell, argv=argv, cwd=cwd, env=env)
