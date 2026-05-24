"""Interactive terminal selection utilities for CLI commands.

Usage:
    from agent.cli.utils.interactive import pick_from_list, resolve_id

    goal_id = resolve_id(
        partial="4c77",          # may be None, full UUID, or prefix
        candidates=goals,        # list of dicts with "id", "status", "created_at"
        label="goal",
        display=lambda g: f"{g['id']}  {g.get('status',''):16} {g.get('created_at','')}",
    )
"""
from __future__ import annotations

import os
import sys
import termios
import tty
from typing import Any, Callable


# ── Low-level terminal key reader ──────────────────────────────────────────────

def _getch() -> str:
    """Read one keypress (or escape sequence) from stdin."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            return ch + ch2 + ch3  # e.g. "\x1b[A"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


_KEY_UP    = "\x1b[A"
_KEY_DOWN  = "\x1b[B"
_KEY_ENTER = "\r"
_KEY_CTRL_C = "\x03"
_KEY_Q = "q"


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


# ── Core picker ────────────────────────────────────────────────────────────────

def pick_from_list(
    items: list[Any],
    *,
    title: str = "Select an item",
    display: Callable[[Any], str] | None = None,
    stream=None,
) -> Any | None:
    """Show an arrow-key-based interactive list on stderr.

    Returns the selected item, or None if cancelled (Ctrl-C / q).
    Falls back to a numbered list when stdin is not a TTY.
    """
    if not items:
        return None

    disp = display or str
    out = stream or sys.stderr

    if not _is_tty():
        # Non-interactive fallback: numbered prompt
        print(f"\n{title}:", file=out)
        for i, item in enumerate(items):
            print(f"  [{i + 1}] {disp(item)}", file=out)
        print(f"  [0] cancel", file=out)
        try:
            raw = input("Enter number: ").strip()
            idx = int(raw)
        except (ValueError, EOFError):
            return None
        if idx == 0 or idx > len(items):
            return None
        return items[idx - 1]

    # Interactive arrow-key picker
    current = 0
    n = len(items)

    def _render(cursor: int) -> None:
        # Move cursor up by (n+2) lines to redraw in place after first render
        lines = n + 2
        print(f"\n\033[1m{title}\033[0m", file=out)
        for i, item in enumerate(items):
            prefix = "\033[32m> \033[0m" if i == cursor else "  "
            print(f"  {prefix}{disp(item)}", file=out)
        print("  (↑↓ navigate, Enter select, q cancel)", file=out)
        out.flush()

    # Initial render
    _render(current)
    # Move cursor up to beginning of rendered block so next render overwrites
    rewind = n + 3  # title + items + footer + blank

    try:
        while True:
            # Rewind
            print(f"\x1b[{rewind}A", end="", file=out)
            _render(current)

            key = _getch()
            if key in (_KEY_CTRL_C, _KEY_Q):
                print(f"\x1b[{rewind}A\x1b[J", end="", file=out)  # clear
                return None
            if key == _KEY_UP:
                current = (current - 1) % n
            elif key == _KEY_DOWN:
                current = (current + 1) % n
            elif key == _KEY_ENTER:
                print(f"\x1b[{rewind}A\x1b[J", end="", file=out)  # clear
                return items[current]
    except Exception:
        return None


# ── ID resolution helpers ──────────────────────────────────────────────────────

def resolve_id(
    partial: str | None,
    candidates: list[dict],
    *,
    label: str = "item",
    display: Callable[[dict], str] | None = None,
    id_key: str = "id",
) -> str | None:
    """Resolve a (possibly partial) UUID from a list of candidates.

    Rules:
    - None or empty → show interactive picker over all candidates
    - Exact UUID match → return it directly
    - Prefix match with exactly 1 result → auto-select
    - Prefix match with multiple results → show picker over matching subset
    - No match → show picker over all candidates with a warning
    """
    if not candidates:
        print(f"No {label}s available.", file=sys.stderr)
        return None

    disp = display or (lambda c: f"{c.get(id_key, '')[:16]}  {c.get('status', ''):16}")

    if not partial:
        selected = pick_from_list(
            candidates,
            title=f"Select {label}",
            display=disp,
        )
        return selected.get(id_key) if selected else None

    # Exact match first
    for c in candidates:
        if c.get(id_key) == partial:
            return partial

    # Prefix match
    prefix = partial.lower()
    matches = [c for c in candidates if (c.get(id_key) or "").lower().startswith(prefix)]

    if len(matches) == 1:
        matched_id = matches[0].get(id_key, "")
        print(f"Resolved {label}: {matched_id}", file=sys.stderr)
        return matched_id

    if len(matches) > 1:
        selected = pick_from_list(
            matches,
            title=f"Multiple {label}s match prefix '{partial}' — select one",
            display=disp,
        )
        return selected.get(id_key) if selected else None

    # No match — warn and show full list
    print(f"Warning: No {label} matches prefix '{partial}'. Showing all.", file=sys.stderr)
    selected = pick_from_list(
        candidates,
        title=f"Select {label} (no match for '{partial}')",
        display=disp,
    )
    return selected.get(id_key) if selected else None


def fetch_goals(base_url: str, token: str, limit: int = 20) -> list[dict]:
    """Fetch recent goals from the hub for interactive selection."""
    try:
        import requests
        r = requests.get(
            f"{base_url}/goals?limit={limit}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.ok:
            data = r.json().get("data") or []
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def fetch_tasks(base_url: str, token: str, goal_id: str = "", limit: int = 30) -> list[dict]:
    """Fetch recent tasks (optionally for one goal) for interactive selection."""
    try:
        import requests
        params = f"limit={limit}"
        if goal_id:
            params += f"&goal_id={goal_id}"
        r = requests.get(
            f"{base_url}/tasks?{params}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.ok:
            data = r.json().get("data") or []
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []
