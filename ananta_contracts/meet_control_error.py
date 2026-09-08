"""Restrictive callback metadata; absence never grants fresh execution authority."""

TERMINAL_HEADER = "X-Ananta-Dialog-Terminal"


def terminal_callback_error(action, code, status):
    return not (
        action == "exchange" and code == "meet_authorization_unavailable" and type(status) is int and status == 503
    )
