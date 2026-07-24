from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import re
import selectors
import signal
import struct
import subprocess
import sys
import termios
import time
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Iterator, Sequence

LOCAL_SCOPE = "local_diagnostic_not_release_evidence"
DEFAULT_TERMINAL_SIZES = ((80, 24), (120, 30), (160, 40))
_MARKER = "[Backlog]PTYCA"
_COLUMNS = (
    "backlog",
    "todo",
    "ready",
    "in_progress",
    "review",
    "blocked",
    "testing",
    "ready_release",
    "completed",
    "cancelled",
)


class _FixtureHubHandler(BaseHTTPRequestHandler):
    cards: tuple[dict[str, Any], ...] = ()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if os.environ.get("ANANTA_TUI_PTY_TRACE") == "1":
            print(f"PTY fixture GET {parsed.path}", file=sys.stderr, flush=True)
        if parsed.path == "/config/features/v1":
            self._send(
                {
                    "data": {
                        "schema": "ananta.dashboard-feature-flags.v1",
                        "features": {
                            "angular_kanban": False,
                            "angular_model_dashboard": False,
                            "tui_kanban": True,
                            "tui_model_menu": False,
                        },
                    }
                }
            )
            return
        if parsed.path == "/api/v1/kanban/boards/hub":
            self._send(
                {
                    "data": {
                        "schema_version": "kanban.v1",
                        "id": "hub",
                        "name": "PTY fixture",
                        "scope_type": "hub",
                        "scope_id": None,
                        "revision": "pty-board-r1",
                        "card_count": len(self.cards),
                        "capabilities": ["kanban.read"],
                        "columns": [
                            {
                                "id": column,
                                "title": column.replace("_", " ").title(),
                                "statuses": [column],
                                "card_count": sum(
                                    card["column_id"] == column
                                    for card in self.cards
                                ),
                            }
                            for column in _COLUMNS
                        ],
                    }
                }
            )
            return
        if parsed.path == "/api/v1/kanban/boards/hub/snapshot":
            board = {
                "schema_version": "kanban.v1",
                "id": "hub",
                "name": "PTY fixture",
                "scope_type": "hub",
                "scope_id": None,
                "revision": "pty-board-r1",
                "card_count": len(self.cards),
                "capabilities": ["kanban.read"],
                "columns": [
                    {
                        "id": column,
                        "title": column.replace("_", " ").title(),
                        "statuses": [column],
                        "card_count": sum(
                            card["column_id"] == column for card in self.cards
                        ),
                    }
                    for column in _COLUMNS
                ],
            }
            self._send(
                {
                    "data": {
                        "schema_version": "kanban.v1",
                        "board": board,
                        "cards": list(self.cards),
                        "event_sequence": 0,
                    }
                }
            )
            return
        if parsed.path == "/api/v1/kanban/boards/hub/events":
            query = urllib.parse.parse_qs(parsed.query)
            sequence = int(query.get("after_sequence", ["0"])[0])
            self._send(
                {
                    "data": {
                        "events": [],
                        "gap_detected": False,
                        "gap_reason": None,
                        "overflow_reason": None,
                        "snapshot_required": False,
                        "snapshot_url": "/api/v1/kanban/boards/hub/snapshot",
                        "next_after_sequence": sequence,
                        "latest_sequence": sequence,
                        "has_more": False,
                    }
                }
            )
            return
        if parsed.path == "/api/v1/kanban/boards/hub/cards":
            query = urllib.parse.parse_qs(parsed.query)
            limit = min(200, max(1, int(query.get("limit", ["200"])[0])))
            offset = max(0, int(query.get("cursor", ["0"])[0]))
            items = self.cards[offset : offset + limit]
            next_offset = offset + len(items)
            self._send(
                {
                    "data": {
                        "schema_version": "kanban.v1",
                        "items": list(items),
                        "next_cursor": (
                            str(next_offset)
                            if next_offset < len(self.cards)
                            else None
                        ),
                    }
                }
            )
            return
        self._send(
            {"error": {"code": "fixture_not_found", "message": "not found"}},
            status=404,
        )

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _fixture_cards(card_count: int) -> tuple[dict[str, Any], ...]:
    cards = []
    for index in range(card_count):
        column = _COLUMNS[index % len(_COLUMNS)]
        cards.append(
            {
                "schema_version": "kanban.v1",
                "id": f"PTY-TASK-{index:04d}",
                "board_id": "hub",
                "title": f"PTYCARD{index:04d}",
                "description": "Deterministic PTY resize fixture",
                "status": column,
                "column_id": column,
                "position": index // len(_COLUMNS),
                "revision": 1,
                "priority": "Medium",
                "assignee": None,
                "labels": ["pty-local-diagnostic"],
                "blocked": column == "blocked",
                "dependencies": [],
                "comment_count": 0,
                "activity_count": 0,
                "created_at": "2026-07-23T12:00:00Z",
                "updated_at": "2026-07-23T12:00:00Z",
            }
        )
    return tuple(cards)


@contextmanager
def fixture_hub(card_count: int) -> Iterator[str]:
    handler = type(
        "KanbanPtyFixtureHandler",
        (_FixtureHubHandler,),
        {"cards": _fixture_cards(card_count)},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_pty_resize_diagnostic(
    *,
    card_count: int = 1000,
    terminal_sizes: Sequence[tuple[int, int]] = DEFAULT_TERMINAL_SIZES,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise RuntimeError("linux_pty_required")
    if card_count < 1 or card_count > 20_000:
        raise ValueError("pty_card_count_invalid")
    sizes = tuple((int(columns), int(rows)) for columns, rows in terminal_sizes)
    if sizes != DEFAULT_TERMINAL_SIZES:
        raise ValueError("pty_terminal_size_matrix_invalid")

    with fixture_hub(card_count) as endpoint:
        measurements, peak_rss_kib = _run_tui_process(
            endpoint=endpoint,
            sizes=sizes,
            timeout_seconds=max(2.0, float(timeout_seconds)),
        )
    return {
        "schema": "ananta.tui-kanban-pty-local-diagnostic.v1",
        "scope": LOCAL_SCOPE,
        "surface": "operator_tui",
        "release_evidence": False,
        "formal_gate_eligible": False,
        "card_count": card_count,
        "terminal_sizes": [
            {"columns": columns, "rows": rows} for columns, rows in sizes
        ],
        "resize_measurements": measurements,
        "peak_rss_kib": peak_rss_kib,
        "diagnostic_status": "passed_local_pty_resize",
    }


def _run_tui_process(
    *,
    endpoint: str,
    sizes: tuple[tuple[int, int], ...],
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], int]:
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    captured = bytearray()
    try:
        _set_terminal_size(master_fd, *sizes[0])
        env = {
            **os.environ,
            "ANANTA_TUI_KANBAN_ENABLED": "true",
            "ANANTA_TUI_MODEL_MENU_ENABLED": "false",
            "ANANTA_TUI_SPLASH": "0",
            "ANANTA_TUI_LOGO": "0",
            "ANANTA_AUTH_TOKEN": "local.pty-fixture.signature",
            "NO_COLOR": "1",
            "TERM": "xterm-256color",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "client_surfaces.operator_tui",
                "--base-url",
                endpoint,
                "--section",
                "kanban",
                "--skip-splash",
                "--no-logo",
            ],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        initial = _read_until(
            master_fd,
            marker=_MARKER.encode("ascii"),
            timeout_seconds=timeout_seconds,
            process=process,
        )
        captured.extend(initial)
        measurements: list[dict[str, Any]] = []
        peak_rss_kib = _rss_kib(process.pid)
        for columns, rows in sizes:
            _drain(master_fd)
            started = time.perf_counter()
            _set_terminal_size(master_fd, columns, rows)
            os.killpg(process.pid, signal.SIGWINCH)
            redraw = _read_until(
                master_fd,
                marker=b"KANBAN",
                timeout_seconds=timeout_seconds,
                process=process,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            captured.extend(redraw)
            peak_rss_kib = max(peak_rss_kib, _rss_kib(process.pid))
            measurements.append(
                {
                    "columns": columns,
                    "rows": rows,
                    "redraw_latency_ms": round(latency_ms, 3),
                    "marker_present": b"KANBAN" in redraw,
                    "process_alive": process.poll() is None,
                }
            )
        if process.poll() is not None:
            raise RuntimeError(
                f"tui_process_exited_during_resize:{process.returncode}"
            )
        return measurements, peak_rss_kib
    finally:
        if process is not None and process.poll() is None:
            try:
                os.write(master_fd, b"\x03")
                process.wait(timeout=2)
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def _set_terminal_size(fd: int, columns: int, rows: int) -> None:
    if columns < 20 or rows < 10:
        raise ValueError("pty_terminal_size_invalid")
    fcntl.ioctl(
        fd,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", rows, columns, 0, 0),
    )


def _read_until(
    fd: int,
    *,
    marker: bytes,
    timeout_seconds: float,
    process: subprocess.Popen[bytes],
) -> bytes:
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"tui_process_exited:{process.returncode}")
            for _key, _mask in selector.select(timeout=0.05):
                try:
                    chunk = os.read(fd, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    continue
                output.extend(chunk)
                if marker.decode("utf-8") in _plain_pty_text(bytes(output[-262_144:])):
                    return bytes(output)
        raise TimeoutError(
            "tui_marker_timeout:"
            f"{marker.decode(errors='replace')}:"
            f"{_redacted_pty_tail(bytes(output))}"
        )
    finally:
        selector.close()


def _drain(fd: int) -> None:
    os.set_blocking(fd, False)
    try:
        while True:
            try:
                if not os.read(fd, 65_536):
                    break
            except BlockingIOError:
                break
    finally:
        os.set_blocking(fd, True)


def _rss_kib(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _redacted_pty_tail(raw: bytes, *, limit: int = 2000) -> str:
    text = _plain_pty_text(raw)
    text = re.sub(
        r"(?i)(authorization|bearer|access[_-]?token|refresh[_-]?token|password)"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[redacted]",
        text,
    )
    return text[-max(256, int(limit)) :]


def _plain_pty_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = "".join(character for character in text if character.isprintable() or character == "\n")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a real Linux PTY resize diagnostic for the Kanban TUI."
    )
    parser.add_argument("--cards", type=int, default=1000)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = run_pty_resize_diagnostic(
        card_count=args.cards,
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
