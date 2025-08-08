import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
import urllib.error

from common.http_client import http_get, http_post


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{\"foo\": \"bar\"}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def run(server):
    with server:
        server.serve_forever()


class HttpClientTests(unittest.TestCase):
    def test_http_get_and_post(self):
        server = HTTPServer(("localhost", 0), Handler)
        thread = threading.Thread(target=run, args=(server,), daemon=True)
        thread.start()
        url = f"http://localhost:{server.server_port}"

        self.assertEqual(http_get(url), {"foo": "bar"})
        self.assertEqual(http_post(url, {"a": 1}), {"a": 1})

        server.shutdown()

    def test_retry_logic(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"{\"ok\": true}"
        resp.__enter__.return_value = resp

        side_effects = [urllib.error.URLError("fail"), resp]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            data = http_get("http://example", retries=2, delay=0)
            self.assertEqual(data, {"ok": True})
            self.assertEqual(urlopen.call_count, 2)

    def test_no_side_effects_on_import(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            import importlib

            import common.http_client as hc
            importlib.reload(hc)
            urlopen.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
