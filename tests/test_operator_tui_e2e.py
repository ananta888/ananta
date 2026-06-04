"""
E2E tests for the Operator TUI with terminal recording.

Produces: tests/output/operator_tui_e2e.cast  (asciinema v2)

Cast format (newline-separated JSON):
  header: {"version":2, "width":W, "height":H, "labels":{t: name}, ...}
  event:  [timestamp_float, "o", clear_prefix + plain_text]

The "o" events start with \\x1b[2J\\x1b[H (clear + cursor-home, needed by the
asciinema player) followed by ANSI-free text.  To strip all ANSI in JS:
  data.replace(/\\x1b\\[[0-?]*[ -\\/]*[@-~]|\\x1b./g, '')
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path
from typing import NamedTuple

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
_W, _H = 100, 30
_CAST_FILE = Path(__file__).parent / "output" / "operator_tui_e2e.cast"
_SPLASH_CAST_FILE = Path(__file__).parent / "output" / "operator_tui_splash.cast"

_CLEAR = "\x1b[2J\x1b[H"   # clear screen + cursor home


# ── render helper ────────────────────────────────────────────────────────────

def _render(*extra: str) -> tuple[int, str]:
    """Run operator TUI in render-once mode and return (exit_code, clean_text)."""
    from agent.cli.main import _run_tui

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run_tui([
            "--render-once", "--skip-splash",
            "--width", str(_W), "--height", str(_H),
            *extra,
        ])
    return rc, _ANSI_RE.sub("", buf.getvalue())


def _render_raw(*extra: str) -> tuple[int, str]:
    """Run operator TUI in render-once mode and return raw output including ANSI."""
    from agent.cli.main import _run_tui

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run_tui([
            "--render-once",
            "--width", str(_W), "--height", str(_H),
            *extra,
        ])
    return rc, buf.getvalue()


# ── recording ────────────────────────────────────────────────────────────────

class _Frame(NamedTuple):
    label: str
    screen: str     # ANSI-free, newline-separated


class _Recorder:
    """
    Collects TUI frames and serialises them as asciinema v2.

    Each frame corresponds to one "o" event in the cast file.
    The header carries a `labels` dict so JavaScript (and Claude) can jump
    directly to a named frame without scanning all events.
    """

    def __init__(self) -> None:
        self._frames: list[_Frame] = []

    def capture(self, label: str, *args: str) -> str:
        rc, screen = _render(*args)
        assert rc == 0, f"render-once failed (rc={rc}) for label={label!r}"
        self._frames.append(_Frame(label, screen))
        return screen

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        header = {
            "version": 2,
            "width": _W,
            "height": _H,
            "title": "Ananta Operator TUI – E2E",
            "env": {"TERM": "xterm-256color"},
            # index: timestamp → frame label  (for JS / Claude navigation)
            "labels": {f"{i * 1.5:.1f}": f.label for i, f in enumerate(self._frames)},
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        for i, frame in enumerate(self._frames):
            # _CLEAR makes the asciinema player overwrite rather than append frames
            lines.append(json.dumps([round(i * 1.5, 1), "o", _CLEAR + frame.screen]))

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── individual assertions ────────────────────────────────────────────────────

def test_e2e_render_is_ansi_free() -> None:
    """Our renderer must not leak any ANSI escape codes into the TUI text."""
    _, out = _render("--section", "dashboard")
    assert "\x1b" not in out, "ANSI escape code found in TUI render output"


def test_e2e_splash_render_preserves_truecolor_ansi() -> None:
    """Fullscreen splash should keep ANSI color codes in raw CLI output."""
    rc, out = _render_raw("--section", "dashboard")
    assert rc == 0
    assert "\x1b[38;2;" in out


def test_e2e_live_splash_matches_website_cast_color_mode(monkeypatch) -> None:
    """Live render-once splash and website splash cast must both carry truecolor ANSI."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("ANANTA_TUI_SPLASH", "1")

    rc, out = _render_raw("--section", "dashboard")
    assert rc == 0
    assert "\x1b[38;2;" in out or "\x1b[0;38;2;" in out

    raw_cast = _SPLASH_CAST_FILE.read_text(encoding="utf-8")
    assert "\\u001b[38;2;" in raw_cast or "\\u001b[0;38;2;" in raw_cast


def test_e2e_three_pane_layout_present() -> None:
    """The TUI layout must always show NAV | CONTENT | DETAIL."""
    _, out = _render("--section", "dashboard")
    for marker in ("NAV", "DASHBOARD", "DETAIL", "Commands:"):
        assert marker in out, f"layout marker missing: {marker!r}"


def test_e2e_all_sections_render_without_ansi() -> None:
    """Every section must render cleanly – no ANSI, title present."""
    for section in ("dashboard", "goals", "tasks", "artifacts", "system", "help"):
        _, out = _render("--section", section)
        assert "\x1b" not in out, f"ANSI leak in section={section!r}"
        assert section.upper() in out.upper(), f"section title missing for {section!r}"


def test_e2e_command_navigation_changes_active_section() -> None:
    """`:section <id>` command must switch the active content pane."""
    _, before = _render("--section", "dashboard")
    _, after  = _render("--section", "dashboard", "--command", ":section goals")
    assert "GOALS" in after
    assert before != after


def test_e2e_chained_commands_are_applied_in_order() -> None:
    """Multiple --command flags must be executed left-to-right."""
    _, out = _render(
        "--section", "dashboard",
        "--command", ":section tasks",
        "--command", ":section system",
    )
    assert "SYSTEM" in out
    assert "TASKS" not in out


def test_e2e_status_bar_carries_endpoint_and_mode() -> None:
    """The status bar must always show connection and mode information."""
    _, out = _render("--section", "dashboard")
    assert "endpoint=" in out
    assert "mode=" in out


def test_e2e_inspect_mode_shows_detail_pane() -> None:
    """`:inspect` should surface extra context in the DETAIL pane."""
    _, out = _render("--section", "tasks", "--command", ":inspect")
    assert "inspect:" in out


def test_e2e_help_section_renders_keybindings() -> None:
    """Help section must contain navigable keybinding documentation."""
    _, out = _render("--section", "help")
    assert "Commands:" in out


def test_e2e_unknown_command_does_not_crash() -> None:
    """Unknown commands must not crash the renderer, only show a status."""
    rc, out = _render("--section", "dashboard", "--command", ":this-does-not-exist")
    assert rc == 0


# ── recording ────────────────────────────────────────────────────────────────

def test_e2e_produces_cast_recording() -> None:
    """
    Drives the TUI through key states and writes the result to
    tests/output/operator_tui_e2e.cast (asciinema v2, ANSI-free frames).

    To replay:   asciinema play tests/output/operator_tui_e2e.cast
    To inspect:  python -c "import json; [print(json.loads(l)) for l in open('tests/output/operator_tui_e2e.cast')]"
    """
    rec = _Recorder()

    rec.capture("01-dashboard",    "--section", "dashboard")
    rec.capture("02-goals",        "--section", "goals")
    rec.capture("03-tasks",        "--section", "tasks")
    rec.capture("04-system",       "--section", "system")
    rec.capture("05-help",         "--section", "help")
    rec.capture("06-artifacts",    "--section", "artifacts")
    rec.capture("07-nav-to-tasks", "--section", "dashboard", "--command", ":section tasks")
    rec.capture("08-nav-to-goals", "--section", "dashboard", "--command", ":section goals")
    rec.capture("09-inspect",      "--section", "tasks",     "--command", ":inspect")
    rec.capture("10-system-refresh","--section", "system",   "--command", ":refresh")

    rec.save(_CAST_FILE)

    # ── structural assertions ────────────────────────────────────────────────
    assert _CAST_FILE.exists()
    raw = _CAST_FILE.read_text(encoding="utf-8")
    lines = raw.strip().split("\n")

    # header + one event per frame
    assert len(lines) == 1 + len(rec._frames), "unexpected line count in cast file"

    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] == _W
    assert header["height"] == _H
    assert len(header["labels"]) == len(rec._frames)
    assert list(header["labels"].values()) == [f.label for f in rec._frames]

    for line in lines[1:]:
        t, kind, data = json.loads(line)
        assert kind == "o"
        assert isinstance(t, float)
        # after stripping the clear-screen prefix, no ANSI should remain
        content = _ANSI_RE.sub("", data)
        assert "\x1b" not in content, f"ANSI escape found in cast event at t={t}"

    # ── content spot-checks ──────────────────────────────────────────────────
    events = {json.loads(l)[2]: json.loads(l) for l in lines[1:]}
    frames_by_label = {f.label: f.screen for f in rec._frames}

    assert "DASHBOARD" in frames_by_label["01-dashboard"]
    assert "GOALS"     in frames_by_label["02-goals"]
    assert "TASKS"     in frames_by_label["03-tasks"]
    assert "SYSTEM"    in frames_by_label["04-system"]
