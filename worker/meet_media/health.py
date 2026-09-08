"""Fixed local liveness probe; neither GPU readiness nor Hub execution authority."""

from contextlib import suppress
from http.client import HTTPConnection, HTTPException

HEALTH_BODY = b'{"schema":"ananta.meet-worker-health.v1","state":"alive"}'


def worker_is_alive(*, connection_factory=HTTPConnection):
    connection = None
    try:
        connection = connection_factory("127.0.0.1", 8094, timeout=2)
        connection.request("GET", "/healthz", headers={"Connection": "close"})
        response = connection.getresponse()
        if (
            response.status != 200
            or response.getheader("Content-Type") != "application/json"
            or response.getheader("Content-Length") != str(len(HEALTH_BODY))
            or response.getheader("Transfer-Encoding") is not None
        ):
            return False
        return response.read(len(HEALTH_BODY) + 1) == HEALTH_BODY
    except (HTTPException, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            with suppress(OSError):
                connection.close()


if __name__ == "__main__":
    raise SystemExit(0 if worker_is_alive() else 1)
