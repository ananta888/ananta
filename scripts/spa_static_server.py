#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.server
import os
import posixpath
import socketserver
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _is_spa_route(path: str) -> bool:
    clean_path = urlsplit(path).path
    basename = posixpath.basename(clean_path.rstrip("/"))
    return "." not in basename


def build_spa_handler(directory: Path) -> type[http.server.SimpleHTTPRequestHandler]:
    root = directory.resolve()

    class SpaStaticHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def send_head(self):
            requested = self.translate_path(self.path)
            if not os.path.exists(requested) and _is_spa_route(self.path):
                self.path = "/index.html"
            return super().send_head()

        def translate_path(self, path: str) -> str:
            parsed_path = urlsplit(path).path
            decoded_path = posixpath.normpath(unquote(parsed_path))
            parts = [part for part in decoded_path.split("/") if part and part not in {".", ".."}]
            result = root
            for part in parts:
                result = result / part
            return str(result)

    return SpaStaticHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a built SPA with index.html fallback for browser routes.")
    parser.add_argument("directory", type=Path, help="Directory containing index.html and built assets.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4200)
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"index.html not found in {directory}")

    class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        if hasattr(socketserver.ThreadingTCPServer, "allow_reuse_port"):
            allow_reuse_port = True

    handler = build_spa_handler(directory)
    with ReusableThreadingTCPServer((args.host, args.port), handler) as httpd:
        print(f"Serving SPA from {directory} at http://{args.host}:{args.port}/")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
