from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import termios


class CarbonylNotAvailableError(RuntimeError):
    """Raised when the carbonyl binary cannot be found or executed."""


class CarbonylRunner:
    """Manage a carbonyl subprocess attached to a PTY.

    Lifecycle:
        runner = CarbonylRunner(carbonyl_binary="carbonyl")
        runner.start(html_path="/tmp/doc.html", cols=80, rows=24)
        data = runner.read_output(timeout=0.05)
        runner.stop()

    The subprocess is killed on stop() or __del__.  Never raises during cleanup.
    """

    def __init__(self, *, carbonyl_binary: str = "carbonyl") -> None:
        self._binary = carbonyl_binary
        self._proc: subprocess.Popen[bytes] | None = None
        self._master_fd: int = -1
        self._slave_fd: int = -1

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, html_path: str, cols: int, rows: int, *, extra_args: list[str] | None = None) -> None:
        """Start carbonyl for html_path/URL with given terminal dimensions.

        Args:
            html_path: Path or URL to open.
            cols: Terminal column count (width).
            rows: Terminal row count (height).
            extra_args: Additional Carbonyl flags before the path/URL.

        Raises:
            CarbonylNotAvailableError: If the binary is not found.
            RuntimeError: If already running.
        """
        if self._proc is not None and self._proc.poll() is None:
            raise RuntimeError("CarbonylRunner is already running; call stop() first")

        resolved = self._resolve_binary()
        self._master_fd, self._slave_fd = pty.openpty()
        self._set_pty_size(self._master_fd, cols=cols, rows=rows)
        # Make master fd non-blocking for read_output
        _set_nonblocking(self._master_fd)

        try:
            self._proc = subprocess.Popen(
                [resolved, "--cols", str(cols), "--rows", str(rows), *(extra_args or []), html_path],
                stdin=self._slave_fd,
                stdout=self._slave_fd,
                stderr=self._slave_fd,
                close_fds=True,
            )
        except (FileNotFoundError, PermissionError) as exc:
            self._close_fds()
            raise CarbonylNotAvailableError(
                f"Failed to start carbonyl binary '{resolved}': {exc}"
            ) from exc

    def stop(self) -> None:
        """Stop the carbonyl subprocess and clean up PTY file descriptors."""
        self._terminate_proc()
        self._close_fds()

    def resize(self, cols: int, rows: int) -> None:
        """Propagate new terminal dimensions to the carbonyl PTY."""
        if self._master_fd >= 0:
            try:
                self._set_pty_size(self._master_fd, cols=cols, rows=rows)
            except OSError:
                pass
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGWINCH)
            except (ProcessLookupError, OSError):
                pass

    def is_running(self) -> bool:
        """Return True if the subprocess is alive."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def read_output(self, timeout: float = 0.05) -> bytes:
        """Non-blocking read from PTY master.

        Returns up to 65536 bytes of output, or b"" if nothing is available.
        """
        if self._master_fd < 0:
            return b""
        try:
            ready, _, _ = select.select([self._master_fd], [], [], max(0.0, timeout))
        except (ValueError, OSError):
            return b""
        if not ready:
            return b""
        try:
            return os.read(self._master_fd, 65536)
        except OSError:
            return b""

    def write_input(self, data: bytes) -> None:
        """Forward keyboard/mouse input bytes to the carbonyl PTY."""
        if self._master_fd < 0 or not data:
            return
        try:
            os.write(self._master_fd, data)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_binary(self) -> str:
        binary = self._binary.strip() or "carbonyl"
        if os.path.isabs(binary):
            if os.path.isfile(binary) and os.access(binary, os.X_OK):
                return binary
            raise CarbonylNotAvailableError(
                f"Carbonyl binary not found or not executable: {binary}"
            )
        resolved = shutil.which(binary)
        if resolved:
            return resolved
        raise CarbonylNotAvailableError(
            f"Carbonyl binary '{binary}' not found on PATH. "
            "Install carbonyl or configure carbonyl_binary in operator_tui.center_browser."
        )

    @staticmethod
    def _set_pty_size(fd: int, *, cols: int, rows: int) -> None:
        """Set PTY window size via TIOCSWINSZ ioctl."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except (AttributeError, OSError):
            pass

    def _terminate_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    def _close_fds(self) -> None:
        for attr in ("_slave_fd", "_master_fd"):
            fd = getattr(self, attr, -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, -1)

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass


def _set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
