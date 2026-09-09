"""Single-flight execution boundary, authenticated by a Hub-only scoped key."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from ananta_contracts.meet_media_failures import (
    MediaCapabilityError,
    failure_from_exit,
    failure_reply,
    failure_signature,
)
from worker.meet_media.contract import (
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    authenticate,
    encode,
    load_key,
    signature,
    validate_turn,
)
from worker.meet_media.http_server import BoundedWorkerServer


class TurnExecutor:
    def __init__(self, replay_path):
        self.lock = threading.Lock()
        self.replay_path = replay_path
        with sqlite3.connect(self.replay_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, deadline INTEGER NOT NULL)")

    def execute(self, turn):
        if not self.lock.acquire(blocking=False):
            raise ValueError("meet_worker_busy")
        try:
            validate_turn(turn, time.time())
            with sqlite3.connect(self.replay_path) as db:
                db.execute("DELETE FROM leases WHERE deadline < ?", (int(time.time()) - 120,))
                try:
                    db.execute("INSERT INTO leases VALUES (?, ?)", (turn["lease_id"], turn["deadline"]))
                except sqlite3.IntegrityError:
                    raise ValueError("meet_turn_replayed") from None
            return self._run(turn)
        finally:
            self.lock.release()

    def _run(self, turn):
        # Capture to a bounded-runtime temporary file rather than an unbounded pipe.
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [sys.executable, "-m", "worker.meet_media.local_runtime"],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                process.communicate(encode(turn), timeout=max(0.01, turn["deadline"] - time.time()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                raise ValueError("meet_turn_deadline_exceeded") from None
            if process.returncode:
                code = failure_from_exit(process.returncode)
                if code is not None:
                    raise MediaCapabilityError(code)
                raise ValueError("meet_local_media_execution_failed")
            output.seek(0)
            raw = output.read(MAX_RESULT_BYTES + 1)
            if len(raw) > MAX_RESULT_BYTES:
                raise ValueError("meet_turn_result_too_large")
            validate_turn(turn, time.time())
            return {
                "schema": "ananta.meet-turn-result.v1",
                "task_id": turn["task_id"],
                "lease_id": turn["lease_id"],
                **json.loads(raw),
            }


def create_server(address, key, executor, dialog_executor=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            from worker.meet_media.health_route import write_health_response

            write_health_response(self)

        def do_POST(self):
            self.connection.settimeout(5)
            body = b""
            turn = None
            capability_failure = False
            dialog = self.path == "/v1/dialogs"
            resources = self.path == "/v1/dialog-resources"
            try:
                if self.path not in {"/v1/turns", "/v1/dialogs", "/v1/dialog-resources"} or self.headers.get(
                    "Transfer-Encoding"
                ):
                    raise ValueError("meet_turn_request_invalid")
                if resources and (
                    len(self.headers.get_all("Content-Length", [])) != 1
                    or self.headers.get_all("Transfer-Encoding", [])
                ):
                    raise ValueError("meet_dialog_resources_request_invalid")
                length = int(self.headers.get("Content-Length", "0"))
                maximum = 1024 if resources else 16384 if dialog else MAX_REQUEST_BYTES
                if not 0 < length <= maximum:
                    raise ValueError("meet_turn_request_invalid")
                from worker.meet_media.persona_http import read_bounded

                body = read_bounded(self.rfile, maximum=maximum, length=length, deadline=time.monotonic() + 5)
                if resources:
                    from worker.meet_media.dialog_resources import observe_resources

                    result, status = (
                        observe_resources(
                            key, body, self.headers.get("X-Ananta-Resources-Signature", ""), dialog_executor
                        ),
                        200,
                    )
                elif dialog:
                    import hmac

                    from ananta_contracts.meet_dialog import parse, request_signature, validate_assignment

                    if dialog_executor is None:
                        raise ValueError("meet_dialog_disabled")
                    if not hmac.compare_digest(
                        request_signature(key, body), self.headers.get("X-Ananta-Dialog-Signature", "")
                    ):
                        raise ValueError("meet_dialog_unauthorized")
                    result, status = dialog_executor.start(validate_assignment(parse(body), time.time())), 200
                else:
                    authenticate(key, body, self.headers.get("X-Ananta-Task-Signature", ""))
                    turn = validate_turn(json.loads(body), time.time())
                    result, status = executor.execute(turn), 200
            except MediaCapabilityError as exc:
                status = 503
                capability_failure = turn is not None and not dialog
                result = (
                    failure_reply(turn, str(exc)) if capability_failure else {"error": {"code": "meet_worker_failed"}}
                )
            except (ValueError, TimeoutError) as exc:
                reason = str(exc)
                if not reason.startswith("meet_") or len(reason) > 90:
                    reason = "meet_turn_request_invalid"
                result, status = {"error": {"code": reason}}, 409
            except Exception:
                result, status = {"error": {"code": "meet_worker_failed"}}, 503
            raw = encode(result)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Ananta-Result-Signature", signature(key, b"result-v1\0" + raw))
            if capability_failure:
                self.send_header("X-Ananta-Media-Failure-Signature", failure_signature(key, body, raw))
            if dialog:
                from ananta_contracts.meet_dialog import response_signature

                self.send_header("X-Ananta-Dialog-Signature", response_signature(key, body, raw))
            if resources:
                from ananta_contracts.meet_dialog_resources import response_signature

                self.send_header("X-Ananta-Resources-Signature", response_signature(key, body, raw))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return BoundedWorkerServer(address, Handler, slots=8, connection_seconds=130)


if __name__ == "__main__":
    key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
    Path("/state").mkdir(exist_ok=True)
    dialog = None
    if os.environ.get("MEET_DIALOG_ENABLED") == "1":
        from worker.meet_media.dialog_executor import DialogExecutor

        dialog = DialogExecutor("/state/dialog-leases.sqlite")
    create_server(("0.0.0.0", 8094), key, TurnExecutor("/state/leases.sqlite"), dialog).serve_forever()
