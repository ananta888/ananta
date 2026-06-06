"""Tests for the TUI :ask mode renderer (middle pane).

Prüft dass _content_chat_plain_ask_lines() eine AI-Antwort vollständig
im Mittleren-Bereich der TUI rendert — ohne LMStudio, mit simuliertem game-state.

Abgedeckte Szenarien:
- Frage gestellt, Antwort da → vollständige Antwort sichtbar
- Streaming partial → streaming text hat Vorrang vor history
- Warte-Zustand → "warte auf Antwort" wird angezeigt
- Chrome bleibt fixiert beim Scrollen (Titel + Frage + Separator + Sender)
- Scroll-Indicator erscheint bei langer Antwort
- Frage + Antwort über Ananta/CodeCompass sind vollständig enthalten
"""
from __future__ import annotations

import re

import pytest

from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.renderer import (
    _content_chat_plain_ask_lines,
    _is_chat_ask_mode,
    _latest_ai_message_text,
)

ANSI_ESCAPE = re.compile(r"\x1b\[[\d;]*[mA-Za-z]|\x1b\(B")


def _strip(s: str) -> str:
    return ANSI_ESCAPE.sub("", s)


def _make_game(
    question: str,
    answer: str | None = None,
    *,
    streaming_partial: str | None = None,
    scroll_offset: int = 0,
) -> dict:
    messages = [{"sender_kind": "user", "sender_id": "user", "text": question}]
    if answer is not None:
        messages.append({"sender_kind": "ai", "sender_id": "AI-Snake", "text": answer})
    game: dict = {
        "tutor_ask_question": question,
        "chat_state": {
            "active_channel": "room:main",
            "channels": {
                "room:main": {
                    "channel_id": "room:main",
                    "messages": messages,
                }
            },
        },
        "chat_long_message_scroll_offset": scroll_offset,
    }
    if streaming_partial is not None:
        game["llm_streaming_partial"] = streaming_partial
    return game


def _render(game: dict, width: int = 88, height: int = 30) -> list[str]:
    state = OperatorState(
        endpoint="http://localhost:7860",
        focus=FocusPane.CONTENT,
        header_logo_game=game,
    )
    lines = _content_chat_plain_ask_lines(state, width, height=height)
    assert lines is not None, "_content_chat_plain_ask_lines returned None"
    return [_strip(l) for l in lines]


# ---------------------------------------------------------------------------
# is_chat_ask_mode
# ---------------------------------------------------------------------------

def test_is_chat_ask_mode_true_when_question_set():
    assert _is_chat_ask_mode({"tutor_ask_question": "Was ist CodeCompass?"}) is True


def test_is_chat_ask_mode_false_when_empty():
    assert _is_chat_ask_mode({}) is False
    assert _is_chat_ask_mode({"tutor_ask_question": ""}) is False
    assert _is_chat_ask_mode({"tutor_ask_question": "  "}) is False


# ---------------------------------------------------------------------------
# Basis: Frage + Antwort vollständig sichtbar
# ---------------------------------------------------------------------------

CODECOMPASS_QUESTION = (
    "Was ist CodeCompass und wie haengt es mit dem snake/ask propose flow zusammen?"
)
CODECOMPASS_ANSWER = (
    "CodeCompass ist der Retrieval-Service in Ananta. "
    "Er liefert beim snake/ask-Propose-Flow relevante Dateipfade und Symbole.\n\n"
    "Der Ablauf:\n"
    "  1. TUI sendet Frage via POST /snake/ask an den Hub\n"
    "  2. ChatPromptBuilder buendelt CodeCompass-Refs in den Prompt\n"
    "  3. LMStudio antwortet mit Bezug auf client_surfaces/operator_tui/chat_prompt_builder.py\n"
    "  4. Antwort erscheint via _content_chat_plain_ask_lines() im Mittleren-Bereich"
)


def test_codecompass_answer_fully_rendered():
    game = _make_game(CODECOMPASS_QUESTION, CODECOMPASS_ANSWER)
    lines = _render(game, width=88, height=40)
    combined = "\n".join(lines)

    assert "AI-SNAKE ANTWORT" in combined, "Pane-Titel muss sichtbar sein"
    assert "CodeCompass" in combined, "Frage-Echo muss CodeCompass enthalten"
    assert "Retrieval-Service" in combined, "Antwort muss Retrieval-Service enthalten"
    assert "chat_prompt_builder.py" in combined, "Dateipfad aus Antwort muss sichtbar sein"
    assert "_content_chat_plain_ask_lines" in combined, "Renderer-Symbol muss sichtbar sein"
    assert "AI-Snake:" in combined, "Sender-Label muss sichtbar sein"


def test_question_echo_is_first_body_line():
    game = _make_game(CODECOMPASS_QUESTION, CODECOMPASS_ANSWER)
    lines = _render(game, width=88, height=40)
    question_line = next((l for l in lines if "CodeCompass" in l and "?" in l), None)
    assert question_line is not None, "Frage muss als Echo erscheinen"
    # Frage muss vor der Antwort stehen
    answer_line = next((l for l in lines if "Retrieval-Service" in l), None)
    assert answer_line is not None
    assert lines.index(question_line) < lines.index(answer_line)


# ---------------------------------------------------------------------------
# Streaming partial hat Vorrang
# ---------------------------------------------------------------------------

def test_streaming_partial_takes_precedence_over_history():
    game = _make_game(
        CODECOMPASS_QUESTION,
        answer="fertiger Text (sollte nicht erscheinen)",
        streaming_partial="Streaming läuft gerade...",
    )
    latest = _latest_ai_message_text(game)
    assert latest is not None
    assert "Streaming" in latest[1], "streaming_partial muss Vorrang haben"
    assert "fertiger Text" not in latest[1]


def test_streaming_partial_rendered_in_middle_pane():
    game = _make_game(
        CODECOMPASS_QUESTION,
        streaming_partial="CodeCompass liefert... (kommt noch)",
    )
    lines = _render(game, width=88, height=20)
    combined = "\n".join(lines)
    assert "CodeCompass liefert" in combined


# ---------------------------------------------------------------------------
# Warte-Zustand (Frage gestellt, noch keine Antwort)
# ---------------------------------------------------------------------------

def test_waiting_state_shows_waiting_indicator():
    game = _make_game(CODECOMPASS_QUESTION, answer=None)
    lines = _render(game, width=88, height=20)
    combined = "\n".join(lines)
    assert "warte" in combined.lower(), "Wartezustand muss 'warte' anzeigen"


def test_waiting_state_still_shows_question_echo():
    game = _make_game(CODECOMPASS_QUESTION, answer=None)
    lines = _render(game, width=88, height=20)
    combined = "\n".join(lines)
    assert "CodeCompass" in combined


# ---------------------------------------------------------------------------
# Chrome-Fixierung beim Scrollen
# ---------------------------------------------------------------------------

LONG_ANSWER = "\n".join(
    [f"Zeile {i:02d}: " + "X" * 40 for i in range(60)]
)


def test_chrome_stays_fixed_when_scrolled():
    game_start = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=0)
    game_scrolled = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=20)

    lines_start = _render(game_start, width=88, height=20)
    lines_scrolled = _render(game_scrolled, width=88, height=20)

    # Chrome (Zeilen 0-3: Titel, Frage-Echo, Separator, Sender) muss identisch sein
    chrome_start = lines_start[:4]
    chrome_scrolled = lines_scrolled[:4]
    assert chrome_start == chrome_scrolled, (
        f"Chrome muss beim Scrollen fixiert bleiben.\n"
        f"Start:    {chrome_start}\n"
        f"Scrolled: {chrome_scrolled}"
    )


def test_body_content_changes_when_scrolled():
    game_start = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=0)
    game_scrolled = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=20)

    lines_start = _render(game_start, width=88, height=20)
    lines_scrolled = _render(game_scrolled, width=88, height=20)

    body_start = "\n".join(lines_start[4:])
    body_scrolled = "\n".join(lines_scrolled[4:])
    assert body_start != body_scrolled, "Body muss sich beim Scrollen aendern"


# ---------------------------------------------------------------------------
# Scroll-Indicator
# ---------------------------------------------------------------------------

def test_scroll_indicator_appears_for_long_answer():
    game = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=0)
    lines = _render(game, width=88, height=20)
    last_line = lines[-1]
    # Indicator zeigt "Zeilen N-M/TOTAL" und Pfeile
    assert "Zeilen" in last_line, f"Scroll-Indicator fehlt in: {last_line!r}"
    assert "/" in last_line


def test_scroll_indicator_shows_anfang_at_top():
    game = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=0)
    lines = _render(game, width=88, height=20)
    assert "Anfang" in lines[-1], f"'Anfang' erwartet bei offset=0: {lines[-1]!r}"


def test_scroll_indicator_shows_ende_at_bottom():
    game = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER, scroll_offset=99999)
    lines = _render(game, width=88, height=20)
    assert "Ende" in lines[-1], f"'Ende' erwartet bei max offset: {lines[-1]!r}"


def test_no_scroll_indicator_when_answer_fits():
    short_answer = "Kurze Antwort.\nNoch eine Zeile."
    game = _make_game(CODECOMPASS_QUESTION, short_answer)
    lines = _render(game, width=88, height=30)
    combined = "\n".join(lines)
    assert "Zeilen" not in combined, "Kein Scroll-Indicator bei kurzer Antwort"


# ---------------------------------------------------------------------------
# height=None (kein Limit)
# ---------------------------------------------------------------------------

def test_no_height_limit_returns_full_content():
    game = _make_game(CODECOMPASS_QUESTION, LONG_ANSWER)
    state = OperatorState(
        endpoint="http://localhost:7860",
        focus=FocusPane.CONTENT,
        header_logo_game=game,
    )
    lines = _content_chat_plain_ask_lines(state, 88, height=None)
    assert lines is not None
    plain = "\n".join(_strip(l) for l in lines)
    # Alle 60 Zeilen der langen Antwort müssen sichtbar sein
    assert "Zeile 59" in plain, "Alle Zeilen bei height=None sichtbar"
    assert "Zeilen" not in plain, "Kein Scroll-Indicator bei height=None"


# ---------------------------------------------------------------------------
# _copy_ask_answer_to_game: stale mouse_selection_range must not affect :ask copy
# ---------------------------------------------------------------------------

class _MinimalCopyHost:
    """Minimal stub to exercise _copy_ask_answer_to_game and _clear_selection_state."""

    def _copy_to_clipboard_bg(self, text: str) -> None:
        self._last_clipboard = text

    def _copy_to_system_clipboard(self, text: str) -> bool:
        return True

    _last_clipboard: str = ""

    from client_surfaces.operator_tui.snake_ops_mixin import SnakeOpsMixin
    _copy_ask_answer_to_game = SnakeOpsMixin._copy_ask_answer_to_game
    _clear_selection_state = SnakeOpsMixin._clear_selection_state


def _make_ask_game_with_stale_range(question: str, answer: str) -> dict:
    return {
        "tutor_ask_question": question,
        "chat_state": {
            "active_channel": "room:main",
            "channels": {
                "room:main": {
                    "channel_id": "room:main",
                    "messages": [
                        {"sender_kind": "user", "sender_id": "user", "text": question},
                        {"sender_kind": "ai", "sender_id": "AI-Snake", "text": answer},
                    ],
                }
            },
        },
        # Stale drag range left over from a previous selection
        "mouse_selection_range": {"start_x": 0, "start_y": 5, "end_x": 80, "end_y": 12, "mode": "linear"},
        "mouse_selection_committed_ranges": [],
        "mouse_selection_active": False,
    }


def test_copy_ask_answer_preserves_spaces():
    answer = "CodeCompass ist der Retrieval-Service in Ananta."
    game = _make_ask_game_with_stale_range("Was ist CodeCompass?", answer)
    host = _MinimalCopyHost()
    host._copy_ask_answer_to_game(game)
    assert "CodeCompass ist der Retrieval-Service" in host._last_clipboard, (
        "Spaces must be preserved in clipboard text"
    )
    assert "CodeCompassist" not in host._last_clipboard


def test_copy_ask_answer_includes_question_echo():
    answer = "Kurze Antwort."
    game = _make_ask_game_with_stale_range("Meine Frage?", answer)
    host = _MinimalCopyHost()
    host._copy_ask_answer_to_game(game)
    assert "Frage: Meine Frage?" in host._last_clipboard


def test_clear_selection_state_resets_all_fields():
    game: dict = {
        "mouse_selection_range": {"start_x": 0, "start_y": 5, "end_x": 80, "end_y": 12, "mode": "linear"},
        "mouse_selection_committed_ranges": [{"start_x": 0, "start_y": 1, "end_x": 20, "end_y": 2, "mode": "linear"}],
        "mouse_selection_active": True,
        "mouse_selection_dragged": True,
        "selection_cells": [(1, 2), (3, 4)],
        "selection_regions": [(0, 1, 20, 2)],
        "selection_anchor": (0, 5),
    }
    host = _MinimalCopyHost()
    host._clear_selection_state(game)
    assert game["mouse_selection_range"] is None
    assert game["mouse_selection_committed_ranges"] == []
    assert game["mouse_selection_active"] is False
    assert game["selection_cells"] == []
    assert game["selection_regions"] == []
