from __future__ import annotations

import os
import queue
import subprocess
import threading
from dataclasses import dataclass

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

    def __post_init__(self) -> None:
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue(maxsize=4096)
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
        self.process = subprocess.Popen(  # noqa: S603
            [self.shell],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
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
            except OSError:
                break

    def write(self, data: str) -> None:
        if self.master_fd is None:
            return
        _write_all(self.master_fd, data.encode("utf-8", errors="ignore"))

    def drain(self) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    def close(self) -> None:
        self._stop.set()
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

    def __post_init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    @property
    def is_pty(self) -> bool:
        return False

    def start(self) -> None:
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
            except Exception:
                break

    def write(self, data: str) -> None:
        if not self.process or not self.process.stdin:
            return
        try:
            self.process.stdin.write(data.encode("utf-8", errors="ignore"))
            self.process.stdin.flush()
        except Exception:
            pass

    def drain(self) -> list[str]:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return chunks

    def close(self) -> None:
        self._stop.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass


def build_terminal_bridge(shell: str) -> PtyBridge | PipeBridge:
    if os.name == "nt":
        return PipeBridge(shell=shell)
    try:
        import pty  # noqa: F401
    except Exception:
        return PipeBridge(shell=shell)
    return PtyBridge(shell=shell)
