"""Closed transient-read signal; never includes response contents or authority."""

from urllib.error import HTTPError

from ananta_contracts.http_read_failure import transient_http_read_error
from ananta_contracts.meet_control_error import TERMINAL_HEADER


class ControlReadUnavailable(ValueError):
    def __init__(self):
        super().__init__("meet_dialog_control_transport_unavailable")


def transient_control_read(action, error):
    if action != "exchange":
        return False
    if isinstance(error, HTTPError):
        # Even an unknown/empty restriction may only stop recovery. Never parse
        # an unsigned error body as permission, policy or refreshed authority.
        if error.headers is not None and error.headers.get(TERMINAL_HEADER) is not None:
            return False
    return transient_http_read_error(error)
