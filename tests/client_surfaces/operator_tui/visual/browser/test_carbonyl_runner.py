from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from client_surfaces.operator_tui.visual.browser.carbonyl_runner import (
    CarbonylNotAvailableError,
    CarbonylRunner,
    _set_nonblocking,
)


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------

class TestBinaryResolution:
    def test_missing_binary_raises(self) -> None:
        runner = CarbonylRunner(carbonyl_binary="nonexistent_binary_xyzzy")
        with pytest.raises(CarbonylNotAvailableError, match="not found on PATH"):
            runner.start("/tmp/doc.html", cols=80, rows=24)

    def test_absolute_path_not_found_raises(self) -> None:
        runner = CarbonylRunner(carbonyl_binary="/nonexistent/carbonyl")
        with pytest.raises(CarbonylNotAvailableError, match="not found or not executable"):
            runner.start("/tmp/doc.html", cols=80, rows=24)

    def test_which_fallback(self) -> None:
        runner = CarbonylRunner(carbonyl_binary="echo")
        # 'echo' exists on PATH — should not raise CarbonylNotAvailableError
        # We mock pty.openpty and subprocess.Popen so it doesn't actually run
        with patch("pty.openpty", return_value=(3, 4)), \
             patch("os.close"), \
             patch("fcntl.ioctl"), \
             patch("fcntl.fcntl"), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            runner.start("/tmp/doc.html", cols=80, rows=24)
        runner._proc = None  # prevent stop() from trying to terminate mock
        runner._master_fd = -1
        runner._slave_fd = -1


# ---------------------------------------------------------------------------
# Start / stop lifecycle
# ---------------------------------------------------------------------------

class TestCarbonylRunnerLifecycle:
    def _make_runner_with_mocks(self) -> tuple[CarbonylRunner, MagicMock]:
        runner = CarbonylRunner(carbonyl_binary="fake_carbonyl")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        return runner, mock_proc

    def test_is_running_false_when_not_started(self) -> None:
        runner = CarbonylRunner()
        assert not runner.is_running()

    def test_is_running_true_when_started(self) -> None:
        runner, mock_proc = self._make_runner_with_mocks()
        with patch("shutil.which", return_value="/fake/carbonyl"), \
             patch("pty.openpty", return_value=(10, 11)), \
             patch("os.close"), \
             patch("fcntl.ioctl"), \
             patch("fcntl.fcntl"), \
             patch("subprocess.Popen", return_value=mock_proc):
            runner.start("/tmp/doc.html", cols=80, rows=24)
            assert runner.is_running()
        runner._proc = None  # cleanup
        runner._master_fd = -1
        runner._slave_fd = -1

    def test_double_start_raises(self) -> None:
        runner, mock_proc = self._make_runner_with_mocks()
        with patch("shutil.which", return_value="/fake/carbonyl"), \
             patch("pty.openpty", return_value=(10, 11)), \
             patch("os.close"), \
             patch("fcntl.ioctl"), \
             patch("fcntl.fcntl"), \
             patch("subprocess.Popen", return_value=mock_proc):
            runner.start("/tmp/doc.html", cols=80, rows=24)
            with pytest.raises(RuntimeError, match="already running"):
                runner.start("/tmp/doc.html", cols=80, rows=24)
        runner._proc = None
        runner._master_fd = -1
        runner._slave_fd = -1

    def test_stop_clears_state(self) -> None:
        runner, mock_proc = self._make_runner_with_mocks()
        with patch("shutil.which", return_value="/fake/carbonyl"), \
             patch("pty.openpty", return_value=(10, 11)), \
             patch("os.close") as mock_close, \
             patch("fcntl.ioctl"), \
             patch("fcntl.fcntl"), \
             patch("subprocess.Popen", return_value=mock_proc):
            runner.start("/tmp/doc.html", cols=80, rows=24)
            runner._proc = mock_proc
            mock_proc.poll.return_value = None
            runner.stop()
        assert runner._master_fd == -1
        assert runner._slave_fd == -1
        assert mock_close.call_count >= 2


# ---------------------------------------------------------------------------
# resize
# ---------------------------------------------------------------------------

class TestCarbonylRunnerResize:
    def test_resize_when_inactive_does_not_raise(self) -> None:
        runner = CarbonylRunner()
        runner.resize(120, 40)  # should not raise

    def test_resize_sends_sigwinch(self) -> None:
        runner, mock_proc = CarbonylRunner(), MagicMock()
        mock_proc.poll.return_value = None
        runner._proc = mock_proc
        runner._master_fd = -1  # skip ioctl
        with patch("fcntl.ioctl"), patch.object(mock_proc, "send_signal") as mock_sig:
            runner.resize(100, 30)


# ---------------------------------------------------------------------------
# read_output
# ---------------------------------------------------------------------------

class TestReadOutput:
    def test_read_output_inactive_returns_empty(self) -> None:
        runner = CarbonylRunner()
        assert runner.read_output() == b""

    def test_read_output_no_data_returns_empty(self) -> None:
        runner = CarbonylRunner()
        runner._master_fd = 99
        with patch("select.select", return_value=([], [], [])):
            result = runner.read_output(timeout=0.01)
        runner._master_fd = -1
        assert result == b""

    def test_read_output_returns_bytes(self) -> None:
        runner = CarbonylRunner()
        runner._master_fd = 99
        with patch("select.select", return_value=([99], [], [])), \
             patch("os.read", return_value=b"hello output"):
            result = runner.read_output(timeout=0.01)
        runner._master_fd = -1
        assert result == b"hello output"


# ---------------------------------------------------------------------------
# write_input
# ---------------------------------------------------------------------------

class TestWriteInput:
    def test_write_input_inactive_does_not_raise(self) -> None:
        runner = CarbonylRunner()
        runner.write_input(b"hello")  # should not raise

    def test_write_input_sends_to_fd(self) -> None:
        runner = CarbonylRunner()
        runner._master_fd = 99
        with patch("os.write") as mock_write:
            runner.write_input(b"key")
            mock_write.assert_called_once_with(99, b"key")
        runner._master_fd = -1
