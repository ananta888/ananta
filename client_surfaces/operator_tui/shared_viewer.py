"""SS05.03: Read-only Viewer-Modus in der TUI.

- Teilnehmer sieht die Owner-TUI read-only
- Viewer kann keine lokalen Owner-Aktionen auslösen
- Zeigt klar 'READ ONLY VIEW OF <owner>'
- Lokaler Chat bleibt bedienbar
- Bei Verbindungsverlust: letzter Snapshot sichtbar, Status disconnected/stale
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedViewerState:
    owner_id: str = ""
    session_id: str = ""
    current_text: str = ""
    current_hash: str = ""
    last_update: float = 0.0
    is_connected: bool = False
    is_stale: bool = False
    is_disconnected: bool = False
    frame_count: int = 0

    @property
    def status_label(self) -> str:
        if self.is_disconnected:
            return "DISCONNECTED"
        if self.is_stale:
            return "STALE"
        if not self.is_connected:
            return "WAITING"
        return "LIVE"

    @property
    def header_line(self) -> str:
        owner = self.owner_id[:30] if self.owner_id else "unknown"
        status = self.status_label
        return f"  ◉ READ ONLY VIEW OF {owner}  [{status}]"


_STALE_THRESHOLD = 30.0  # Sekunden ohne Update → stale


class SharedViewer:
    """Verwaltet den Read-only-Viewer-Modus für einen Teilnehmer."""

    def __init__(self, session_id: str, owner_id: str) -> None:
        self._state = SharedViewerState(session_id=session_id, owner_id=owner_id)

    @property
    def state(self) -> SharedViewerState:
        return self._state

    def apply_frame(self, text: str, frame_hash: str) -> None:
        """Aktualisiert den angezeigten Snapshot."""
        self._state = SharedViewerState(
            owner_id=self._state.owner_id,
            session_id=self._state.session_id,
            current_text=text,
            current_hash=frame_hash,
            last_update=time.time(),
            is_connected=True,
            is_stale=False,
            is_disconnected=False,
            frame_count=self._state.frame_count + 1,
        )

    def mark_disconnected(self) -> None:
        self._state = SharedViewerState(
            owner_id=self._state.owner_id,
            session_id=self._state.session_id,
            current_text=self._state.current_text,
            current_hash=self._state.current_hash,
            last_update=self._state.last_update,
            is_connected=False,
            is_stale=True,
            is_disconnected=True,
            frame_count=self._state.frame_count,
        )

    def tick(self, now: float | None = None) -> None:
        """Prüft ob der View stale ist."""
        t = float(now if now is not None else time.time())
        if self._state.is_connected and self._state.last_update > 0:
            if t - self._state.last_update > _STALE_THRESHOLD:
                self._state = SharedViewerState(
                    owner_id=self._state.owner_id,
                    session_id=self._state.session_id,
                    current_text=self._state.current_text,
                    current_hash=self._state.current_hash,
                    last_update=self._state.last_update,
                    is_connected=True,
                    is_stale=True,
                    is_disconnected=False,
                    frame_count=self._state.frame_count,
                )


def render_shared_viewer_lines(viewer_state: SharedViewerState, *, width: int = 80, height: int = 24) -> list[str]:
    """Rendert den Read-only-Viewer-Inhalt."""
    lines: list[str] = []
    header = viewer_state.header_line
    sep = "─" * min(width - 2, 78)
    if viewer_state.is_disconnected:
        color = "\x1b[31m"
    elif viewer_state.is_stale:
        color = "\x1b[33m"
    else:
        color = "\x1b[32m"
    lines.append(f"  {color}{header}\x1b[0m")
    lines.append(f"  {sep}")
    if not viewer_state.current_text:
        lines.append("")
        lines.append("  Warte auf ersten Snapshot vom Owner…")
        return lines
    content_lines = viewer_state.current_text.splitlines()
    max_lines = max(1, height - 4)
    for cl in content_lines[:max_lines]:
        lines.append(cl)
    if len(content_lines) > max_lines:
        remaining = len(content_lines) - max_lines
        lines.append(f"  \x1b[90m… {remaining} weitere Zeilen\x1b[0m")
    return lines


def is_viewer_action_blocked(action: str) -> bool:
    """Read-only: alle mutativen Aktionen werden blockiert."""
    blocked = {
        "goal_create", "goal_delete", "task_create", "task_delete",
        "artifact_write", "artifact_delete", "config_write",
        "snake_command", "execute",
    }
    return action in blocked
