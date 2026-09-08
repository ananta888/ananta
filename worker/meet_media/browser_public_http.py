"""Strict bounded HTTP/1 document reader for the one-shot public fetch process."""

import re
import time

from ananta_contracts.browser_public_fetch import MAX_DOCUMENT_BYTES

_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


class PublicDocumentReader:
    """No redirects, compression, authentication, downloads, or retained cookies."""

    def __init__(self, stream, *, deadline: float, clock=time.monotonic):
        self.stream, self.deadline, self.clock = stream, deadline, clock

    def _current(self):
        if self.clock() >= self.deadline:
            raise ValueError("browser_public_fetch_expired")

    def _line(self, limit):
        if limit < 2:
            raise ValueError("browser_public_headers_too_large")
        self._current()
        value = self.stream.readline(limit + 1)
        self._current()
        if len(value) > limit or not value.endswith(b"\r\n"):
            raise ValueError("browser_public_http_invalid")
        return value[:-2]

    def _exact(self, size):
        output = bytearray()
        while len(output) < size:
            self._current()
            chunk = self.stream.read(min(65536, size - len(output)))
            self._current()
            if not chunk:
                raise ValueError("browser_public_http_incomplete")
            output.extend(chunk)
        return bytes(output)

    def read(self):
        status = self._line(1024)
        if not re.fullmatch(rb"HTTP/1\.[01] 200(?: [\x20-\x7e]*)?", status):
            raise ValueError("browser_public_status_denied")
        headers = self._headers()
        content_type = headers.get(b"content-type", [b""])
        if len(content_type) != 1 or not re.fullmatch(
            rb"text/html(?:\s*;\s*charset\s*=\s*(?:utf-8|\"utf-8\"))?", content_type[0], re.I
        ):
            raise ValueError("browser_public_content_type_denied")
        if any(
            name in headers
            for name in (
                b"content-disposition",
                b"content-encoding",
                b"www-authenticate",
                b"proxy-authenticate",
                b"trailer",
                b"upgrade",
            )
        ):
            raise ValueError("browser_public_content_denied")
        length, transfer = headers.get(b"content-length"), headers.get(b"transfer-encoding")
        if length is not None:
            if transfer is not None or len(length) != 1 or not re.fullmatch(rb"[0-9]{1,7}", length[0]):
                raise ValueError("browser_public_framing_invalid")
            size = int(length[0])
            if not 0 < size <= MAX_DOCUMENT_BYTES:
                raise ValueError("browser_public_body_too_large")
            return self._exact(size)
        if transfer is not None:
            if transfer != [b"chunked"]:
                raise ValueError("browser_public_framing_invalid")
            return self._chunked()
        result = bytearray()
        while len(result) <= MAX_DOCUMENT_BYTES:
            self._current()
            block = self.stream.read(min(65536, MAX_DOCUMENT_BYTES + 1 - len(result)))
            self._current()
            if not block:
                if not result:
                    raise ValueError("browser_public_document_invalid")
                return bytes(result)
            result.extend(block)
        raise ValueError("browser_public_body_too_large")

    def _headers(self):
        headers, size = {}, 0
        for _ in range(65):
            line = self._line(min(4096, 16384 - size))
            size += len(line) + 2
            if not line:
                return headers
            name, separator, value = line.partition(b":")
            if not separator or not _HEADER_NAME.fullmatch(name) or any(c < 32 and c != 9 or c == 127 for c in value):
                raise ValueError("browser_public_http_invalid")
            headers.setdefault(name.lower(), []).append(value.strip(b" \t"))
        raise ValueError("browser_public_headers_too_large")

    def _chunked(self):
        result = bytearray()
        for _ in range(4096):
            size_line = self._line(16)
            if not re.fullmatch(rb"[0-9a-fA-F]{1,8}", size_line):
                raise ValueError("browser_public_chunk_invalid")
            size = int(size_line, 16)
            if size == 0:
                if self._line(2) != b"" or not result:
                    raise ValueError("browser_public_trailer_denied")
                return bytes(result)
            if size + len(result) > MAX_DOCUMENT_BYTES:
                raise ValueError("browser_public_body_too_large")
            result.extend(self._exact(size))
            if self._exact(2) != b"\r\n":
                raise ValueError("browser_public_chunk_invalid")
        raise ValueError("browser_public_chunks_too_many")
