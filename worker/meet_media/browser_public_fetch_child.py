"""Trusted one-shot fetch entry point; parent owns absolute timeout and cancellation."""

import json
import sys
import time

from ananta_contracts.browser_public_fetch import PublicFetchTarget, document_result, validate_fetch_request
from ananta_contracts.persona_inspection_wire import parse_inspection_json
from worker.meet_media.browser_public_connection import public_connection
from worker.meet_media.browser_public_http import PublicDocumentReader


def fetch_document(request, *, connect=public_connection):
    request = validate_fetch_request(request)
    target = PublicFetchTarget.parse(request["url"])
    host = target.origin.split("://", 1)[1]
    deadline = time.monotonic() + 3
    with connect(target) as connection:
        connection.sendall(
            (
                f"GET {target.path} HTTP/1.1\r\nHost: {host}\r\n"
                "Accept: text/html\r\nAccept-Encoding: identity\r\n"
                "User-Agent: Ananta-Public-Document/1\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
        )
        with connection.makefile("rb") as stream:
            return document_result(PublicDocumentReader(stream, deadline=deadline).read())


def main():
    try:
        raw = sys.stdin.buffer.read(8193)
        request = parse_inspection_json(raw, maximum=8192)
        result = fetch_document(request)
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception:
        # Remote status lines, URLs, cookies and exception text never leave child.
        sys.stdout.write('{"error":"browser_public_fetch_failed"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
