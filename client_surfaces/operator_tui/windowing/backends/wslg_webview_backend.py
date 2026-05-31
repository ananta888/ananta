from __future__ import annotations

import os
import shutil

from client_surfaces.operator_tui.windowing.process_runner import run_detached
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


def wslg_available() -> tuple[bool, str]:
    display = str(os.environ.get("DISPLAY") or "").strip()
    wayland = str(os.environ.get("WAYLAND_DISPLAY") or "").strip()
    if display or wayland:
        return True, ""
    return False, "no DISPLAY/WAYLAND_DISPLAY"


def _is_wsl2() -> bool:
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except OSError:
        return False


def _resolve_opener(url: str) -> tuple[list[str], str] | tuple[None, str]:
    """Return (argv, opener_name) or (None, error_reason)."""
    override = str(os.environ.get("ANANTA_WINDOW_OPEN_CMD") or "").strip()
    if override:
        if shutil.which(override) is None:
            return None, f"opener missing: {override}"
        return [override, url], override

    if _is_wsl2():
        # In WSL2, xdg-open often misfires (triggers MSRDC, RDP or nothing).
        # Prefer: wslview (wslu package) → powershell Start-Process → explorer.exe
        if shutil.which("wslview"):
            return ["wslview", url], "wslview"
        ps = shutil.which("powershell.exe")
        if ps:
            # Single-quote the URL; standard HTTP URLs contain no single quotes.
            return [ps, "-Command", f"Start-Process '{url}'"], "powershell"
        if shutil.which("explorer.exe"):
            return ["explorer.exe", url], "explorer.exe"
        return None, "no wsl2 browser opener found (install wslu for wslview)"

    # Native Linux / WSLg X11 / Wayland
    if shutil.which("xdg-open"):
        return ["xdg-open", url], "xdg-open"
    return None, "xdg-open not found"


class WslgWebviewBackend:
    backend_name = "wslg_webview"

    def __init__(self) -> None:
        self._state = ExternalWindowState.INACTIVE
        self._pid: int | None = None
        self._reason = ""

    def open_window(self, *, url: str) -> WindowHealth:
        ok, reason = wslg_available()
        if not ok:
            self._state = ExternalWindowState.DEGRADED
            self._reason = reason
            return self.health()

        argv, opener_or_err = _resolve_opener(url)
        if argv is None:
            self._state = ExternalWindowState.DEGRADED
            self._reason = opener_or_err
            return self.health()

        self._state = ExternalWindowState.STARTING
        started = run_detached(argv)
        if not started.ok:
            self._state = ExternalWindowState.FAILED
            self._reason = started.reason
            self._pid = None
            return self.health()

        self._pid = started.pid
        self._state = ExternalWindowState.ACTIVE
        self._reason = ""
        return self.health()

    def close_window(self) -> WindowHealth:
        self._state = ExternalWindowState.INACTIVE
        self._pid = None
        self._reason = ""
        return self.health()

    def health(self) -> WindowHealth:
        return WindowHealth(
            state=self._state,
            backend=self.backend_name,
            pid=self._pid,
            reason=self._reason,
            details={"display": str(os.environ.get("DISPLAY") or ""), "wayland": str(os.environ.get("WAYLAND_DISPLAY") or "")},
        )
