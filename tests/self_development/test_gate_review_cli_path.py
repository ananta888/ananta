"""`gate_review` must reach the CLI propose path (the loop activates there).

`begin_review` writes the research context that "activates the mutation loop",
but the phased review kind (`gate_review`) was missing from the CLI-kind set, so
reviews fell through to the strategy orchestrator and answered (or not) as plain
model text — no loop, no enforced `final_answer` protocol.
"""

from __future__ import annotations

from agent.services._task_scoped_propose_step import LEGACY_CLI_TASK_KINDS


def test_gate_review_reaches_the_cli_path():
    assert "gate_review" in LEGACY_CLI_TASK_KINDS
    assert "review" in LEGACY_CLI_TASK_KINDS


def test_coding_kinds_stay_on_the_cli_path():
    assert {"coding", "analysis", "doc", "ops"} <= LEGACY_CLI_TASK_KINDS


def test_unknown_kinds_do_not_leak_into_the_cli_path():
    assert "codecompass_layer_build" not in LEGACY_CLI_TASK_KINDS
