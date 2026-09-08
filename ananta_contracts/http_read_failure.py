"""Pure closed transport classification, without application authority or retries."""

import errno
import socket
import ssl
from urllib.error import HTTPError, URLError


def transient_http_read_error(error):
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
