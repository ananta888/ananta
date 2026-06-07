"""CRPS-007: tests for the Profile Inspector footer in the TUI AI-Snake
answer pane.

The inspector shows the user which retrieval profile was used for the most
recent :ask. It is a thin footer built by ``_profile_inspector_lines`` and
spliced into the chrome by ``_splice_inspector_into_chrome``. Both functions
live in client_surfaces.operator_tui.renderer and are pure-Python (no I/O,
no LLM, no network).

We test:
  - empty / no trace → 0 lines
  - normal trace → 1 compact line, contains profile_id + domain + intent
  - verbose env-flag toggles a second reasons line + sources line
  - _splice_inspector_into_chrome inserts after the cyan sender line
  - _splice_inspector_into_chrome trims from the bottom when height is set
  - inspector line is visible even when body is long (scroll case)
"""
from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("PYTEST_CURRENT_TEST", "1")

# Force the renderer module to import cleanly.
import client_surfaces.operator_tui.renderer as renderer  # noqa: E402

_profile_inspector_lines = renderer._profile_inspector_lines
_splice_inspector_into_chrome = renderer._splice_inspector_into_chrome


def _make_trace(**overrides: Any) -> dict[str, Any]:
    """Build a realistic last_snake_ask_trace shape."""
    trace: dict[str, Any] = {
        "profile_id": "ananta-codecompass",
        "domain": "codecompass",
        "intent": "implemented_code_explanation",
        "trigger_mode": "auto",
        "feature_flag": "auto",
        "source_types": ["repo", "artifact"],
        "reasons": ["classified_domain:codecompass", "classified_intent:code_explanation"],
    }
    trace.update(overrides)
    return trace


def _make_game_with_trace(trace: dict[str, Any] | None, summary: str = "") -> dict:
    game: dict[str, Any] = {}
    if trace is not None:
        game["last_snake_ask_trace"] = trace
    if summary:
        game["last_snake_ask_summary"] = summary
    return game


class TestProfileInspectorLinesEmpty:
    """When no trace has been captured, the inspector must return []."""

    def test_no_trace_returns_empty(self):
        game: dict = {}
        assert _profile_inspector_lines(game, 80) == []

    def test_none_trace_returns_empty(self):
        game = _make_game_with_trace(None)
        assert _profile_inspector_lines(game, 80) == []

    def test_non_dict_trace_returns_empty(self):
        # Defensive: a corrupted state should not crash the render.
        for bad in ("not-a-dict", 42, ["list"], 3.14):
            game = {"last_snake_ask_trace": bad}
            assert _profile_inspector_lines(game, 80) == [], f"unexpected for {bad!r}"


class TestProfileInspectorLinesCompact:
    """Normal mode (verbose off): exactly one compact line."""

    def test_returns_single_line(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        trace = _make_trace()
        game = _make_game_with_trace(trace)
        lines = _profile_inspector_lines(game, 200)
        assert len(lines) == 1

    def test_compact_line_contains_profile_id(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        # ANSI codes make exact comparison brittle, so check the substring.
        assert "ananta-codecompass" in lines[0]

    def test_compact_line_contains_domain_and_intent(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        # The line shows `d=codecompass` and `i=implemented_code_explanation`
        # (or a clipped version) per the renderer contract.
        assert "d=codecompass" in lines[0]
        assert "i=implemented_code_explanation" in lines[0]

    def test_compact_line_contains_trigger_and_flag(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        assert "trig=auto" in lines[0]
        assert "flag=auto" in lines[0]

    def test_compact_line_includes_summary(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        game = _make_game_with_trace(_make_trace(), summary="Kontext: 7 Treffer (repo:5, artifact:2) [ananta-codecompass]")
        lines = _profile_inspector_lines(game, 200)
        assert "Kontext: 7 Treffer" in lines[0]

    def test_compact_line_no_summary(self, monkeypatch):
        # When no summary is present, the line still renders cleanly.
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        game = _make_game_with_trace(_make_trace())  # no summary
        lines = _profile_inspector_lines(game, 200)
        assert len(lines) == 1
        assert "ananta-codecompass" in lines[0]

    def test_missing_field_falls_back_to_question_mark(self, monkeypatch):
        # An empty/missing field must render as `?` (the renderer's contract).
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        # No profile_id, no intent at all
        trace: dict[str, Any] = {"domain": "codecompass"}
        lines = _profile_inspector_lines(_make_game_with_trace(trace), 200)
        assert "?" in lines[0]  # at least one field is unknown

    def test_width_clipping_does_not_crash(self, monkeypatch):
        # CRPS-007: _clip(..., width) must not break on a tiny width.
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 5)
        assert len(lines) == 1
        # The clipped line must be no longer than the width.
        # (We strip ANSI for the length check.)
        import re
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        visible = ansi_re.sub("", lines[0])
        assert len(visible) <= 5


class TestProfileInspectorLinesVerbose:
    """Verbose mode (env-flag set): 2-3 lines (reasons + sources + optional 3rd)."""

    def test_verbose_env_flag_1_enables_reasons_line(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "1")
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        # The reasons line must be present.
        assert any("reasons:" in ln for ln in lines)

    def test_verbose_env_flag_true_enables_reasons_line(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "true")
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        assert any("reasons:" in ln for ln in lines)

    def test_verbose_env_flag_yes_enables_reasons_line(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "yes")
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        assert any("reasons:" in ln for ln in lines)

    def test_verbose_env_flag_random_does_not_enable(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "gibberish")
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        # No reasons line in compact mode.
        assert not any("reasons:" in ln for ln in lines)

    def test_verbose_includes_sources_line(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "1")
        lines = _profile_inspector_lines(_make_game_with_trace(_make_trace()), 200)
        # The sources line lists repo,artifact
        assert any("sources:" in ln and "repo,artifact" in ln for ln in lines)

    def test_verbose_reasons_truncated_to_5(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "1")
        trace = _make_trace(reasons=[
            "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8",
        ])
        lines = _profile_inspector_lines(_make_game_with_trace(trace), 200)
        # The reasons line must contain the first 5 reasons but not r6.
        reasons_line = next(ln for ln in lines if "reasons:" in ln)
        assert "r1" in reasons_line and "r5" in reasons_line
        assert "r6" not in reasons_line

    def test_verbose_with_empty_reasons_still_renders_sources(self, monkeypatch):
        monkeypatch.setenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", "1")
        trace = _make_trace(reasons=[])
        lines = _profile_inspector_lines(_make_game_with_trace(trace), 200)
        # No reasons line because reasons is empty, but the sources line
        # is still emitted.
        assert not any("reasons:" in ln for ln in lines)
        assert any("sources:" in ln for ln in lines)


class TestSpliceInspectorIntoChrome:
    """The splice function must insert after the cyan sender line and
    trim the bottom when height is set."""

    def test_empty_out_returns_empty(self):
        assert _splice_inspector_into_chrome([], ["x"], None) == []
        assert _splice_inspector_into_chrome([], ["x"], 20) == []

    def test_appends_when_no_sender_line_present(self):
        out = ["line1", "line2"]
        result = _splice_inspector_into_chrome(out, ["INSP"], None)
        # Fallback: append at the end.
        assert result == ["line1", "line2", "INSP"]

    def test_inserts_after_cyan_sender_line(self):
        # The renderer uses cyan (38;2;120;180;255) ANSI for the sender.
        sender_line = "  \x1b[38;2;120;180;255m\x1b[1ms-ai:\x1b[0m"
        out = [
            "title",
            "question",
            sender_line,
            "body line 1",
            "body line 2",
        ]
        result = _splice_inspector_into_chrome(out, ["INSP_A", "INSP_B"], None)
        # The inspector must come directly after the sender, before body.
        assert result.index("INSP_A") == 3
        assert result.index("INSP_B") == 4
        # The total length grew by 2.
        assert len(result) == 7

    def test_height_truncates_from_bottom(self):
        sender_line = "  \x1b[38;2;120;180;255m\x1b[1ms-ai:\x1b[0m"
        out = [
            "title",
            "question",
            sender_line,
            "body 1",
            "body 2",
            "body 3",
        ]
        # height=5 → the bottom 2 lines (one original + one inspector)
        # must be trimmed, but the inspector at the top must remain.
        result = _splice_inspector_into_chrome(out, ["INSP"], 5)
        assert len(result) == 5
        # The inspector is the 4th element (after title, question, sender).
        assert result[3] == "INSP"
        # The last original body line was trimmed away.
        assert "body 3" not in result


class TestProfileInspectorVisibilityDuringScroll:
    """The Profile Inspector must remain visible even when the answer body
    is long and scrolled. We simulate by feeding a body longer than the
    visible height and asserting the inspector line still appears in the
    final output."""

    def test_inspector_visible_with_long_body_and_height(self, monkeypatch):
        monkeypatch.delenv("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE", raising=False)
        sender_line = "  \x1b[38;2;120;180;255m\x1b[1ms-ai:\x1b[0m"
        # A 100-line body, only 10 lines fit in the visible chrome.
        out = [
            "title",
            "question",
            sender_line,
        ] + [f"body line {i}" for i in range(100)]
        # We use _profile_inspector_lines to build the footer and then
        # _splice_inspector_into_chrome to insert it. After that we
        # truncate to height=10 to simulate clipping.
        game = _make_game_with_trace(_make_trace())
        inspector = _profile_inspector_lines(game, 80)
        result = _splice_inspector_into_chrome(out, inspector, 10)
        # The inspector line (single, compact) must be in the visible
        # output. Look for the marker.
        joined = "\n".join(result)
        assert "ananta-codecompass" in joined
        assert "d=codecompass" in joined
        # And the total visible height is 10 lines.
        assert len(result) == 10
