import struct

from agent.services import terminal_bridge as bridge_mod
from agent.services.terminal_bridge import PtyBridge, _write_all


def test_write_all_retries_until_payload_is_fully_written():
    writes: list[bytes] = []

    def fake_write(fd: int, payload: bytes) -> int:
        assert fd == 123
        writes.append(payload)
        return max(1, len(payload) // 2)

    _write_all(123, b"abcdef", writer=fake_write)

    assert writes == [b"abcdef", b"def", b"ef", b"f"]


def test_pty_bridge_write_encodes_text_and_uses_write_all(monkeypatch):
    bridge = PtyBridge(shell="/bin/sh")
    bridge.master_fd = 123
    captured: list[tuple[int, bytes]] = []

    def fake_write_all(fd: int, payload: bytes) -> None:
        captured.append((fd, payload))

    monkeypatch.setattr("agent.services.terminal_bridge._write_all", fake_write_all)

    bridge.write("hello")

    assert captured == [(123, b"hello")]


def test_pty_bridge_resize_updates_window_size(monkeypatch):
    bridge = PtyBridge(shell="/bin/sh")
    bridge.master_fd = 123
    captured: list[tuple[int, int, bytes]] = []

    def fake_ioctl(fd: int, op: int, payload: bytes) -> None:
        captured.append((fd, op, payload))

    monkeypatch.setattr(bridge_mod.fcntl, "ioctl", fake_ioctl)

    bridge.resize(120, 40)

    assert captured == [(123, bridge_mod.termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))]


def test_pty_bridge_wait_for_output_returns_true_when_buffered():
    bridge = PtyBridge(shell="/bin/sh")
    bridge.output_queue.put_nowait("hello")

    assert bridge.wait_for_output(0.01) is True


def test_pty_bridge_wait_for_output_returns_false_without_process_or_data():
    bridge = PtyBridge(shell="/bin/sh")

    assert bridge.wait_for_output(0.01) is False
