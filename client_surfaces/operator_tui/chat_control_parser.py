from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    raw_text: str
    command: str        # "view" | "overlay" | "focus" | "open" | "snake" | "help" | ""
    subcommand: str
    args: tuple[str, ...]
    error: str          # empty = success
    action_id: str

    @property
    def ok(self) -> bool:
        return not self.error


_NL_PHRASE_MAP: dict[str, str] = {
    "nächste view": "/view next",
    "vorherige view": "/view previous",
    "zeige markdown": "/view markdown",
    "view leiste an": "/overlay views on",
    "view leiste aus": "/overlay views off",
    "view leiste": "/overlay views toggle",
    "snake pausieren": "/snake pause",
    "snake fortsetzen": "/snake resume",
}


def is_chat_command(text: str) -> bool:
    t = text.strip()
    return t.startswith("/") and not t.startswith("//")


def parse_chat_command(text: str, *, nl_mode_enabled: bool = False) -> ParsedCommand:
    text = text.strip()

    if nl_mode_enabled and not text.startswith("/"):
        mapped = _NL_PHRASE_MAP.get(text.lower())
        if mapped:
            text = mapped
        else:
            return ParsedCommand(raw_text=text, command="", subcommand="", args=(), error="not a command", action_id="")

    if not text.startswith("/") or text.startswith("//"):
        return ParsedCommand(raw_text=text, command="", subcommand="", args=(), error="not a command", action_id="")

    try:
        tokens = shlex.split(text[1:])
    except ValueError as exc:
        return ParsedCommand(raw_text=text, command="", subcommand="", args=(), error=f"parse error: {exc}", action_id="")

    if not tokens:
        return ParsedCommand(raw_text=text, command="", subcommand="", args=(), error="empty command", action_id="")

    cmd = tokens[0].lower()
    rest = tokens[1:]

    if cmd == "view":
        return _parse_view(text, rest)
    if cmd == "overlay":
        return _parse_overlay(text, rest)
    if cmd == "focus":
        return _parse_focus(text, rest)
    if cmd == "open":
        return _parse_open(text, rest)
    if cmd == "snake":
        return _parse_snake(text, rest)
    if cmd == "scroll":
        return _parse_scroll(text, rest)
    if cmd == "help":
        return _parse_help(text, rest)

    return ParsedCommand(
        raw_text=text, command=cmd, subcommand="", args=(),
        error=f"unknown command /{cmd}; try /help tui for available commands",
        action_id="",
    )


def _parse_view(raw: str, args: list[str]) -> ParsedCommand:
    if not args:
        return ParsedCommand(raw_text=raw, command="view", subcommand="", args=(),
                             error="/view requires: list, next, previous, or <view_id>", action_id="")
    sub = args[0].lower()
    if sub == "list":
        return ParsedCommand(raw_text=raw, command="view", subcommand="list", args=(), error="", action_id="view.list")
    if sub == "next":
        return ParsedCommand(raw_text=raw, command="view", subcommand="next", args=(), error="", action_id="view.next")
    if sub in ("previous", "prev"):
        return ParsedCommand(raw_text=raw, command="view", subcommand="previous", args=(), error="", action_id="view.previous")
    view_id = _normalize_view_id(sub)
    return ParsedCommand(raw_text=raw, command="view", subcommand="select", args=(view_id,), error="", action_id="view.select")


def _normalize_view_id(raw: str) -> str:
    aliases = {
        "markdown": "markdown_mermaid_document",
        "diagnostics": "renderer_diagnostics",
        "snake": "snake_debug",
        "artifact": "artifact_preview",
        "logo": "logo_animation",
        "strategy": "strategy_map_preview",
    }
    return aliases.get(raw.lower(), raw.lower())


def _parse_overlay(raw: str, args: list[str]) -> ParsedCommand:
    if not args:
        return ParsedCommand(raw_text=raw, command="overlay", subcommand="", args=(),
                             error="/overlay requires: views [on|off|toggle]", action_id="")
    sub = args[0].lower()
    if sub == "views":
        modifier = args[1].lower() if len(args) > 1 else "toggle"
        if modifier == "on":
            return ParsedCommand(raw_text=raw, command="overlay", subcommand="views on", args=(), error="", action_id="overlay.views.on")
        if modifier == "off":
            return ParsedCommand(raw_text=raw, command="overlay", subcommand="views off", args=(), error="", action_id="overlay.views.off")
        if modifier in ("toggle", ""):
            return ParsedCommand(raw_text=raw, command="overlay", subcommand="views toggle", args=(), error="", action_id="overlay.views.toggle")
        return ParsedCommand(raw_text=raw, command="overlay", subcommand="views", args=(modifier,),
                             error=f"unknown overlay modifier {modifier!r}; use on, off or toggle", action_id="")
    return ParsedCommand(raw_text=raw, command="overlay", subcommand=sub, args=(),
                         error=f"unknown overlay type {sub!r}; try /overlay views [on|off|toggle]", action_id="")


def _parse_focus(raw: str, args: list[str]) -> ParsedCommand:
    if not args:
        return ParsedCommand(raw_text=raw, command="focus", subcommand="", args=(),
                             error="/focus requires: chat, artifacts, main, diagnostics, center, logs, nav", action_id="")
    target = args[0].lower()
    mapping = {
        "chat": "focus.chat",
        "artifacts": "focus.artifacts",
        "main": "focus.main",
        "diagnostics": "focus.diagnostics",
        "center": "focus.center",
        "logs": "focus.logs",
        "nav": "focus.nav",
    }
    action_id = mapping.get(target)
    if not action_id:
        return ParsedCommand(raw_text=raw, command="focus", subcommand=target, args=(),
                             error=f"unknown focus target {target!r}; use chat, artifacts, main, diagnostics, center, logs, nav", action_id="")
    return ParsedCommand(raw_text=raw, command="focus", subcommand=target, args=(), error="", action_id=action_id)


def _parse_open(raw: str, args: list[str]) -> ParsedCommand:
    if not args or args[0].lower() != "artifact":
        return ParsedCommand(raw_text=raw, command="open", subcommand="", args=(),
                             error="/open requires: /open artifact <index_or_id>", action_id="")
    ref_args = args[1:]
    if not ref_args:
        return ParsedCommand(raw_text=raw, command="open", subcommand="artifact", args=(),
                             error="/open artifact requires an index or id (e.g. /open artifact 3)", action_id="")
    return ParsedCommand(raw_text=raw, command="open", subcommand="artifact", args=(ref_args[0],), error="", action_id="artifact.open")


def _parse_snake(raw: str, args: list[str]) -> ParsedCommand:
    if not args:
        return ParsedCommand(raw_text=raw, command="snake", subcommand="", args=(),
                             error="/snake requires: pause, resume, follow on, follow off", action_id="")
    sub = args[0].lower()
    if sub == "pause":
        return ParsedCommand(raw_text=raw, command="snake", subcommand="pause", args=(), error="", action_id="snake.pause")
    if sub == "resume":
        return ParsedCommand(raw_text=raw, command="snake", subcommand="resume", args=(), error="", action_id="snake.resume")
    if sub == "follow":
        mod = args[1].lower() if len(args) > 1 else ""
        if mod == "on":
            return ParsedCommand(raw_text=raw, command="snake", subcommand="follow on", args=(), error="", action_id="snake.follow.on")
        if mod == "off":
            return ParsedCommand(raw_text=raw, command="snake", subcommand="follow off", args=(), error="", action_id="snake.follow.off")
        return ParsedCommand(raw_text=raw, command="snake", subcommand="follow", args=(),
                             error="/snake follow requires on or off", action_id="")
    return ParsedCommand(raw_text=raw, command="snake", subcommand=sub, args=(),
                         error=f"unknown snake subcommand {sub!r}; use pause, resume, follow on/off", action_id="")


def _parse_scroll(raw: str, args: list[str]) -> ParsedCommand:
    if not args:
        return ParsedCommand(raw_text=raw, command="scroll", subcommand="", args=(),
                             error="/scroll requires: up, down, pageup, pagedown, top, bottom", action_id="")
    sub = args[0].lower()
    mapping = {
        "up": "scroll.line_up",
        "down": "scroll.line_down",
        "pageup": "scroll.page_up",
        "pagedown": "scroll.page_down",
        "top": "scroll.home",
        "home": "scroll.home",
        "bottom": "scroll.end",
        "end": "scroll.end",
    }
    action_id = mapping.get(sub)
    if not action_id:
        return ParsedCommand(raw_text=raw, command="scroll", subcommand=sub, args=(),
                             error=f"unknown scroll direction {sub!r}; use up, down, pageup, pagedown, top, bottom", action_id="")
    return ParsedCommand(raw_text=raw, command="scroll", subcommand=sub, args=(), error="", action_id=action_id)


def _parse_help(raw: str, args: list[str]) -> ParsedCommand:
    return ParsedCommand(raw_text=raw, command="help", subcommand="tui", args=(), error="", action_id="help.tui")
