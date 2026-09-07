"""Bounded private stdio observation and exactly owned process cleanup."""

import json
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path


class SilentRoomDriver:
    def __init__(self, repository, public_key):
        self.process = subprocess.Popen(
            ["node", str(Path(__file__).with_name("meet_silent_room_bridge.mjs"))],
            cwd=repository,
            env=os.environ | {"MEET_SILENT_ROOM_GATE": "1", "MEET_TEST_HUB_PUBLIC_KEY": str(public_key)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pending = b""
        self.closed = False

    def receive(self, timeout=20):
        if type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("silent_bridge_timeout_invalid")
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("silent_bridge_read_timeout")
                chunk = os.read(self.process.stdout.fileno(), 2049)
                if not chunk or len(self.pending) + len(chunk) > 2048:
                    raise ValueError("silent_bridge_reply_invalid")
                self.pending += chunk
        raw, self.pending = self.pending.split(b"\n", 1)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("silent_bridge_failed")
        if "bridge_error" in value:
            stage = value.get("stage")
            stage = stage if isinstance(stage, str) and re.fullmatch(r"[a-z-]{1,48}", stage) else "unknown"
            raise ValueError("silent_bridge_failed:" + stage)
        return value

    def command(self, line, *, timeout=20):
        if not isinstance(line, str) or re.fullmatch(r"inspect|join-human|bind room-[a-f0-9]{18}", line) is None:
            raise ValueError("silent_bridge_command_invalid")
        self.process.stdin.write((line + "\n").encode())
        self.process.stdin.flush()
        return self.receive(timeout)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
            try:
                code = self.process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGTERM)
                try:
                    code = self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._signal(signal.SIGKILL)
                    code = self.process.wait(timeout=5)
            if code != 0:
                raise ValueError("silent_bridge_cleanup_failed")
        finally:
            self.process.stdout.close()

    def _signal(self, value):
        try:
            os.killpg(self.process.pid, value)
        except ProcessLookupError:
            pass
