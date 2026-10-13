"""Closed chat diagnostics: counts and validated IDs, never text or payloads."""

import re


def chat_event_diagnostic(kind, event):
    if kind not in {"RECV", "REPLY"}:
        raise ValueError("chat_diagnostic_kind_invalid")
    identity = event.get("message_id") if isinstance(event, dict) else None
    marker = identity[:8] if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{32}", identity) else "invalid"
    return f"{kind} id={marker}"


def chat_state_diagnostic(state):
    state = state if isinstance(state, dict) else {}
    peers, chat = state.get("peers"), state.get("chat")
    peer_count = len(peers) if isinstance(peers, (list, dict)) else peers if type(peers) is int else 0
    chat_count = len(chat) if isinstance(chat, list) else 0
    encrypted = state.get("e2ee") is True
    return f"STATE peers={max(0, min(peer_count, 10000))} e2ee={encrypted} chat={chat_count}"


def renewal_status_diagnostic(status):
    """HTTP outcome only; callers must not read the renewal response body."""
    code = status if type(status) is int and 100 <= status <= 599 else "invalid"
    return f"RENEW status={code}"


def browser_console_diagnostic(kind, *, e2ee=False):
    """Never return browser text, exceptions or authority values."""
    if kind in {"error", "warning"}:
        return f"CONSOLE level={kind} event=browser"
    if kind == "log" and e2ee is True:
        return "CONSOLE level=log event=e2ee"
    return None
