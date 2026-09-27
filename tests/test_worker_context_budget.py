"""LCTX-012: every iteration prompt of the ananta-worker loops stays within the 32k window."""

from __future__ import annotations

import pytest

from agent import context_window as cw
from agent.cli_backends import context_budget as cb

pytestmark = pytest.mark.timeout(30)
WINDOW = 32768


def _fits(prompt: str) -> bool:
    return cw.estimate_tokens(prompt) <= WINDOW - cb.ANSWER_RESERVE_TOKENS


def test_available_room_is_the_window_minus_reserve_and_fixed_parts():
    assert cb.available_chars("x" * 4000, window_tokens=10_000, reserve_tokens=1000) == (10_000 - 1000 - 1000) * 4
    assert cb.available_chars("x" * 4000, window_tokens=10_000, reserve_tokens=1000, cap_chars=500) == 500
    assert cb.available_chars("x" * 400_000, window_tokens=10_000) == 0


def test_fit_blocks_keeps_the_newest_condenses_older_and_notes_what_is_left_out():
    blocks = [f"## Schritt {i}\n" + "x" * 3000 for i in range(10)]
    with cw.truncation_scope() as events:
        fitted = cb.fit_blocks(blocks, 9000, site="batch_loop.progress")
    assert fitted[-1] == blocks[-1] and fitted[-2] == blocks[-2]  # newest complete
    assert any("[verdichtet" in block for block in fitted)  # older condensed
    assert fitted[0].startswith("[") and "ausgelassen" in fitted[0]  # oldest left out, with a note
    assert sum(len(b) for b in fitted[1:]) <= 9000
    assert events[0].site == "batch_loop.progress" and events[0].detail["left_out"] > 0
    assert cb.fit_blocks(["a", "b"], 100, site="x") == ["a", "b"]


def test_the_tool_loop_prompt_fits_the_window_even_with_a_large_task():
    from agent.cli_backends.tool_loop import build_tool_loop_prompt

    task = "Auftrag mit Hub-Kontext: " + "k" * 60_000  # ~15k tokens of task alone
    results = [{"tool_name": f"repo.read_file_range#{i}", "status": "ok", "data": {"text": str(i % 10) * 7900}}
               for i in range(12)]
    prompt = build_tool_loop_prompt(original_prompt=task, instructions="Regeln", tool_results=results,
                                    iteration=12, max_iterations=12, max_tool_result_chars=8000,
                                    max_total_tool_result_chars=400_000)
    assert _fits(prompt)
    assert "1" * 7900 in prompt  # the newest result (index 11) is complete
    assert "repo.read_file_range#0" in prompt or "ausgelassen" in prompt  # older ones condensed or noted


def test_the_batch_loop_no_longer_loses_early_findings_silently():
    from agent.cli_backends.architecture_scan import _build_iteration_prompt

    progress = "\n\n---\n\n".join(f"## Schritt {i}\nErkenntnis {i}: " + "e" * 2500 for i in range(1, 9))
    batch = [{"rel_path": "a.py", "content": "print(1)\n" * 400, "lang": "python"}]
    prompt = _build_iteration_prompt("Analysiere das Repo", batch=batch, progress_so_far=progress, step=9,
                                     total_steps=10)
    assert _fits(prompt)
    for i in range(1, 9):  # 8 steps of ~2.5k chars fit: nothing is cut any more (before: only the last 6000 chars)
        assert f"Erkenntnis {i}:" in prompt
    huge = "\n\n---\n\n".join(f"## Schritt {i}\n" + "e" * 30_000 for i in range(1, 9))
    with cw.truncation_scope() as events:
        prompt = _build_iteration_prompt("Analysiere", batch=batch, progress_so_far=huge, step=9, total_steps=10)
    assert _fits(prompt) and "## Schritt 8" in prompt and events and events[0].site == "batch_loop.progress"


def test_the_mutation_loop_evidence_fits_the_window():
    from agent.cli_backends.workspace_mutation.prompts import build_iteration_prompt

    evidence = [{"tool": "test.run", "output": "F" * 12_000} for _ in range(6)]
    prompt = build_iteration_prompt(original_prompt="Fix " + "t" * 80_000, instructions="Regeln",
                                    evidence_blocks=evidence, iteration=4, max_iterations=6,
                                    max_chars_per_block=12_000)
    assert _fits(prompt)
