from agent.tui_contract import (
    TuiOption,
    TuiPromptState,
    reduce_prompt_key,
    render_prompt_snapshot,
    sanitize_terminal_text,
)


def test_tui_prompt_snapshot_is_stable_and_plain_text():
    state = TuiPromptState(
        title="Goal Aktion",
        options=(
            TuiOption("quick", "Quick Goal", "Schnell planen"),
            TuiOption("guided", "Guided Goal", "Mehr Kontext"),
        ),
        selected_index=1,
    )

    assert render_prompt_snapshot(state) == (
        "Goal Aktion\n"
        "  Quick Goal - Schnell planen\n"
        "> Guided Goal - Mehr Kontext"
    )


def test_tui_prompt_navigation_and_cancel_behavior_are_reproducible():
    state = TuiPromptState(
        title="Aktion waehlen",
        options=(
            TuiOption("analyze", "Analysieren"),
            TuiOption("review", "Reviewen"),
            TuiOption("diagnose", "Diagnose"),
        ),
    )

    down = reduce_prompt_key(state, "down")
    assert down.state.selected_index == 1
    assert reduce_prompt_key(down.state, "enter").selected_id == "review"

    up = reduce_prompt_key(down.state, "up")
    assert up.state.selected_index == 0
    assert reduce_prompt_key(up.state, "escape").cancelled is True


def test_tui_output_sanitizes_terminal_escape_and_control_sequences():
    hostile = "\x1b]8;;https://evil.example\x07click\x1b]8;;\x07\n\x1b[31mFAIL\x1b[0m\rOK\x00"

    assert sanitize_terminal_text(hostile) == "click\nFAIL\nOK"


def test_tui_empty_options_keep_snapshot_and_navigation_safe():
    state = TuiPromptState(title="\x1b[31mEmpty", options=(), selected_index=99, error="No entries\x07")

    result = reduce_prompt_key(state, "enter")

    assert result.selected_id is None
    assert result.cancelled is False
    assert render_prompt_snapshot(result.state) == "Empty\n! No entries"
