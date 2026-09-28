"""A requests session whose in-flight requests can really be stopped from another thread.

``requests.Session.close()`` only closes pooled, idle connections; a connection a blocked thread is using
stays open and the model server keeps generating (live: an aborted proposal kept a llama.cpp slot busy for
minutes). This session tracks every connection it opens and ``close()`` shuts their sockets down, so the
peer sees the disconnect at once (llama.cpp stops the generation) and the blocked call ends with an error.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from requests.sessions import Session as _Session  # the class itself (tests replace ``requests.Session``)
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool


def _tracked(connection_cls: type, registry: "_ConnectionRegistry") -> type:
    class Tracked(connection_cls):  # type: ignore[misc, valid-type]
        def connect(self) -> None:
            super().connect()
            registry.add(self)

        def close(self) -> None:
            registry.discard(self)
            super().close()

    return Tracked


class _ConnectionRegistry:
    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._lock = threading.Lock()

    def add(self, connection: Any) -> None:
        with self._lock:
            self._connections.add(connection)

    def discard(self, connection: Any) -> None:
        with self._lock:
            self._connections.discard(connection)

    def shutdown_all(self) -> int:
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        stopped = 0
        for connection in connections:
            sock = getattr(connection, "sock", None)
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
                stopped += 1
            except OSError:
                pass
        return stopped


class _TrackingAdapter(HTTPAdapter):
    def __init__(self, registry: _ConnectionRegistry, **kwargs: Any) -> None:
        self._registry = registry
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        registry = self._registry

        class Pool(HTTPConnectionPool):
            ConnectionCls = _tracked(HTTPConnection, registry)

        class TlsPool(HTTPSConnectionPool):
            ConnectionCls = _tracked(HTTPSConnection, registry)

        self.poolmanager.pool_classes_by_scheme = {"http": Pool, "https": TlsPool}


def make_cancellable(session: Any) -> Any:
    """Make an existing ``requests.Session`` cancellable in place (other objects, e.g. test doubles, are returned
    unchanged): its connections are tracked and ``close()`` shuts them down."""
    if not isinstance(session, _Session) or isinstance(session, CancellableSession):
        return session
    registry = _ConnectionRegistry()
    adapter = _TrackingAdapter(registry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    original_close = session.close

    def close() -> None:
        registry.shutdown_all()
        original_close()

    session.close = close  # type: ignore[method-assign]
    return session


class CancellableSession(_Session):
    """``requests.Session`` whose ``close()`` (from any thread) stops the requests in flight."""

    def __init__(self) -> None:
        super().__init__()
        self._connections = _ConnectionRegistry()
        adapter = _TrackingAdapter(self._connections)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def close(self) -> None:
        self._connections.shutdown_all()
        super().close()


__all__ = ["CancellableSession", "make_cancellable"]
