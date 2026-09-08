"""Bounded, content-free liveness never signs, executes or grants availability."""

import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from worker.meet_media.health import HEALTH_BODY, worker_is_alive
from worker.meet_media.health_route import health_request_allowed
from worker.meet_media.server import create_server

pytestmark = pytest.mark.timeout(10)


@pytest.fixture
def endpoint(monkeypatch):
    executor, dialog, signer = Mock(), Mock(), Mock(side_effect=AssertionError("health must not sign"))
    monkeypatch.setattr("worker.meet_media.server.signature", signer)
    server = create_server(("127.0.0.1", 0), b"synthetic-health-key-32-bytes-long", executor, dialog)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, executor, dialog, signer
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        executor.execute.assert_not_called()
        dialog.start.assert_not_called()
        signer.assert_not_called()


def test_real_http_liveness_and_probe_work_while_executor_is_busy_without_using_a_lease(endpoint):
    server, executor, _, _ = endpoint
    executor.lock = threading.Lock()
    with executor.lock:

        def connect(host, port, *, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 8094, 2)
            return HTTPConnection(*server.server_address, timeout=timeout)

        assert worker_is_alive(connection_factory=connect)
        connection = connect("127.0.0.1", 8094, timeout=2)
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            assert response.status == 200 and response.read() == HEALTH_BODY
            assert response.getheader("Cache-Control") == "no-store"
            assert response.getheader("X-Ananta-Result-Signature") is None
        finally:
            connection.close()


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/", {}),
        ("/healthz?task=foreign", {}),
        ("/healthz/", {}),
        ("/healthz", {"Content-Length": "1"}),
        ("/healthz", {"Transfer-Encoding": "chunked"}),
    ],
)
def test_bad_health_request_is_closed_without_reading_a_body(endpoint, path, headers):
    server, _, _, _ = endpoint
    connection = HTTPConnection(*server.server_address, timeout=1)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        assert response.status == 404
        assert response.read() == b'{"error":{"code":"meet_health_unavailable"}}'
        assert response.getheader("X-Ananta-Result-Signature") is None
    finally:
        connection.close()


@pytest.mark.parametrize("address", ["192.0.2.1", "10.0.0.1", "0.0.0.0", "::", "not-an-ip"])
def test_remote_or_unknown_addresses_never_get_health_details(address):
    assert not health_request_allowed(address, "/healthz", [], [])


def test_body_metadata_cannot_smuggle_an_execution_request():
    assert health_request_allowed("127.0.0.1", "/healthz", ["0"], [])
    assert health_request_allowed("::1", "/healthz", [], [])
    for lengths in (["0", "1"], ["0", "0"], ["-1"], ["bogus"]):
        assert not health_request_allowed("127.0.0.1", "/healthz", lengths, [])


@pytest.mark.parametrize("change", ["status", "body", "length", "type", "encoding", "timeout", "connect"])
def test_probe_fails_closed_and_closes_connection_without_output(change, capsys):
    response = Mock(status=200)
    headers = {"Content-Type": "application/json", "Content-Length": str(len(HEALTH_BODY))}
    response.getheader.side_effect = headers.get
    response.read.return_value = HEALTH_BODY
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    if change == "status":
        response.status = 503
    elif change == "body":
        response.read.return_value = b"private failure details"
    elif change == "length":
        headers["Content-Length"] = "1000000"
    elif change == "type":
        headers["Content-Type"] = "text/plain"
    elif change == "encoding":
        headers["Transfer-Encoding"] = "chunked"
    elif change == "timeout":
        connection.getresponse.side_effect = TimeoutError("private timeout details")
    else:
        factory.side_effect = OSError("private connect details")
    assert not worker_is_alive(connection_factory=factory)
    if change != "connect":
        connection.close.assert_called_once()
    if change in {"status", "length", "type", "encoding"}:
        response.read.assert_not_called()
    assert capsys.readouterr() == ("", "")


def test_image_healthcheck_and_compose_quota_keep_existing_container_boundaries():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "docker/meet-media/Dockerfile").read_text()
    assert (
        'HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 '
        'CMD ["python", "-m", "worker.meet_media.health"]'
        in dockerfile
    )
    assert "COPY worker/meet_media /app/worker/meet_media" in dockerfile
    service = yaml.safe_load((root / "docker-compose.meet-media.yml").read_text())["services"]["meet-media-worker"]
    assert service["cpus"] == "${MEET_MEDIA_CPUS:-4.0}"
    assert service["mem_limit"] == "4g" and service["pids_limit"] == 256
    assert service["cap_drop"] == ["ALL"] and "no-new-privileges:true" in service["security_opt"]
    assert "ports" not in service and not service.get("privileged")
    assert all("docker.sock" not in value and ".config" not in value for value in service["volumes"])
