from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


TuiKey = Literal["up", "down", "enter", "escape"]

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class TuiOption:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class TuiPromptState:
    title: str
    options: tuple[TuiOption, ...]
    selected_index: int = 0
    error: str = ""


@dataclass(frozen=True)
class TuiPromptResult:
    state: TuiPromptState
    selected_id: str | None = None
    cancelled: bool = False


def sanitize_terminal_text(value: object, *, max_chars: int = 240) -> str:
    """Return terminal-safe plain text for prompt snapshots and CLI/TUI output."""
    text = str(value or "")
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    normalized = "\n".join(line for line in lines if line)
    limit = max(1, int(max_chars or 240))
    return normalized[:limit]


def normalize_prompt_state(state: TuiPromptState) -> TuiPromptState:
    options = tuple(
        TuiOption(
            id=sanitize_terminal_text(option.id, max_chars=80),
            label=sanitize_terminal_text(option.label, max_chars=120),
            description=sanitize_terminal_text(option.description, max_chars=180),
        )
        for option in state.options
        if sanitize_terminal_text(option.id, max_chars=80)
    )
    if not options:
        selected_index = 0
    else:
        selected_index = min(max(0, int(state.selected_index or 0)), len(options) - 1)
    return TuiPromptState(
        title=sanitize_terminal_text(state.title, max_chars=160),
        options=options,
        selected_index=selected_index,
        error=sanitize_terminal_text(state.error, max_chars=200),
    )


def render_prompt_snapshot(state: TuiPromptState) -> str:
    normalized = normalize_prompt_state(state)
    lines = [normalized.title or "Auswahl"]
    if normalized.error:
        lines.append(f"! {normalized.error}")
    for index, option in enumerate(normalized.options):
        marker = ">" if index == normalized.selected_index else " "
        suffix = f" - {option.description}" if option.description else ""
        lines.append(f"{marker} {option.label}{suffix}")
    return "\n".join(lines)


def reduce_prompt_key(state: TuiPromptState, key: TuiKey) -> TuiPromptResult:
    normalized = normalize_prompt_state(state)
    if key == "escape":
        return TuiPromptResult(state=normalized, cancelled=True)
    if not normalized.options:
        return TuiPromptResult(state=normalized)
    if key == "enter":
        return TuiPromptResult(state=normalized, selected_id=normalized.options[normalized.selected_index].id)
    if key == "up":
        next_index = max(0, normalized.selected_index - 1)
    elif key == "down":
        next_index = min(len(normalized.options) - 1, normalized.selected_index + 1)
    else:
        next_index = normalized.selected_index
    return TuiPromptResult(state=TuiPromptState(
        title=normalized.title,
        options=normalized.options,
        selected_index=next_index,
        error=normalized.error,
    ))
