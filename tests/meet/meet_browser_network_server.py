"""Private TLS fixtures: fixed assets/redirects and one native WS greeting only."""

import base64
import hashlib
import ipaddress
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-browser-network")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem, private = directory / "synthetic-ca.pem", directory / "synthetic-key.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    private.chmod(0o600)
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem, private, base64.b64encode(hashlib.sha256(spki).digest()).decode()


@contextmanager
def serve(pem, private):
    seen = []
    redirects = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.connection.settimeout(3)
            seen.append(self.path)
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/socket" and self.headers.get("Upgrade", "").lower() == "websocket":
                key = self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
                self.end_headers()
                body = b"synthetic-ready"
                self.wfile.write(bytes([0x81, len(body)]) + body)
                self.wfile.flush()
                self.close_connection = True
                return
            body = b"<!doctype html><title>synthetic</title>" if self.path == "/machine" else b"synthetic-asset"
            if self.path == "/worker.js":
                body = (
                    b"onmessage=e=>{const s=new WebSocket(e.data.url);"
                    b"s.onopen=()=>{postMessage('allowed');s.close();};"
                    b"s.onerror=()=>postMessage('denied');};"
                )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/javascript"
                if self.path == "/worker.js"
                else "text/html"
                if self.path == "/machine"
                else "text/plain",
            )
            self.send_header("Content-Length", str(len(body)))
            if self.path == "/strict":
                self.send_header("Content-Security-Policy", "connect-src 'none'")
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem, private)

    class Server(ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            # Browser speculative TCP connections must not block accept() or
            # fixture shutdown waiting for a TLS handshake that never starts.
            return context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "https://127.0.0.1:" + str(server.server_port), seen, redirects
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()
