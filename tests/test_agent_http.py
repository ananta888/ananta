import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent.ai_agent import _http_get, _http_post


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


def test_http_get_and_post():
    server = HTTPServer(("localhost", 0), Handler)
    thread = threading.Thread(target=run, args=(server,), daemon=True)
    thread.start()
    url = f"http://localhost:{server.server_port}"

    assert _http_get(url) == {"foo": "bar"}
    assert _http_post(url, {"a": 1}) == {"a": 1}

    server.shutdown()
