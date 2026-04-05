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
