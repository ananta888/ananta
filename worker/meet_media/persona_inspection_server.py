"""Bounded signed inspection server; wire profile is operator-owned, not a request."""

import hmac
import time
from http.server import BaseHTTPRequestHandler

from ananta_contracts.persona_inspection_wire import parse_inspection_json
from worker.meet_media.contract import encode
from worker.meet_media.http_server import BoundedWorkerServer
from worker.meet_media.persona_http import read_bounded, request_signature, result_signature


def create_inspection_server(address, key, executor, *, wire):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            raw = b""
            try:
                if self.path != wire.path or self.headers.get("Transfer-Encoding"):
                    raise ValueError(f"persona_{wire.kind}_request_invalid")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= wire.request_limit:
                    raise ValueError(f"persona_{wire.kind}_request_size_invalid")
                raw = read_bounded(
                    self.rfile, maximum=wire.request_limit, deadline=time.monotonic() + 5, length=int(lengths[0])
                )
                if not hmac.compare_digest(
                    request_signature(key, wire.domain, raw), self.headers.get("X-Ananta-Persona-Signature", "")
                ):
                    raise ValueError(f"persona_{wire.kind}_request_unauthorized")
                result, status = executor.execute(parse_inspection_json(raw, maximum=wire.request_limit)), 200
            except (ValueError, PermissionError, TimeoutError):
                result, status = {"error": {"code": f"persona_{wire.kind}_denied_or_invalid"}}, 409
            except Exception:
                result, status = {"error": {"code": f"persona_{wire.kind}_worker_unavailable"}}, 503
            output = encode(result)
            if len(output) > wire.result_limit:
                output, status = encode({"error": {"code": f"persona_{wire.kind}_result_too_large"}}), 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(output)))
            self.send_header("X-Ananta-Persona-Result-Signature", result_signature(key, wire.domain, raw, output))
            self.end_headers()
            self.wfile.write(output)

    return BoundedWorkerServer(address, Handler, slots=4, connection_seconds=25, read_seconds=3)
