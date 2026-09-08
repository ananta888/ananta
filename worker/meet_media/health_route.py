"""Content-free loopback GET adapter, separate from signed execution requests."""

import ipaddress

from worker.meet_media.health import HEALTH_BODY


def health_request_allowed(address, path, lengths, encodings):
    try:
        local = ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False
    return local and path == "/healthz" and lengths in ([], ["0"]) and not encodings


def write_health_response(handler):
    handler.close_connection = True
    allowed = health_request_allowed(
        handler.client_address[0],
        handler.path,
        handler.headers.get_all("Content-Length", []),
        handler.headers.get_all("Transfer-Encoding", []),
    )
    raw = HEALTH_BODY if allowed else b'{"error":{"code":"meet_health_unavailable"}}'
    handler.send_response(200 if allowed else 404)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    try:
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError):
        pass
