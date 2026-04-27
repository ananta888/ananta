"""Optional embedded runtime entry-points for mobile Hub/Worker control.

This module is intentionally lightweight and generic. It exposes stable start/stop
functions so the Android Capacitor plugin can control Python runtime behavior
without importing the full backend stack by default.
"""

from __future__ import annotations

_hub_running = False
_worker_running = False


def start_hub() -> str:
    global _hub_running
    _hub_running = True
    return "hub_started"


def stop_hub() -> str:
    global _hub_running
    _hub_running = False
    return "hub_stopped"


def start_worker() -> str:
    global _worker_running
    _worker_running = True
    return "worker_started"


def stop_worker() -> str:
    global _worker_running
    _worker_running = False
    return "worker_stopped"


def health_check() -> str:
    return f"python_runtime_ok hub={_hub_running} worker={_worker_running}"
