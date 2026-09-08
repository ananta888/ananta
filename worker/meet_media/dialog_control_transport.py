"""Closed transient-read signal; never includes response contents or authority."""

import errno
import socket
import ssl
from urllib.error import HTTPError, URLError


class ControlReadUnavailable(ValueError):
    def __init__(self):
        super().__init__("meet_dialog_control_transport_unavailable")


def transient_control_read(action, error):
    if action != "exchange":
        return False
    if isinstance(error, HTTPError):
        return type(error.code) is int and error.code in {502, 503, 504}
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, ssl.SSLError):
        return False
    if isinstance(cause, socket.gaierror):
        return cause.errno == socket.EAI_AGAIN
    if isinstance(cause, TimeoutError):
        return True
    return isinstance(cause, OSError) and cause.errno in {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }
