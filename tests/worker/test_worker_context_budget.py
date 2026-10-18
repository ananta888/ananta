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


def test_the_mutation_loop_evidence_also_survives_the_prompt_budget_gate():
    """The planner counts ~4 chars per token, the gate counts with the tokenizer + safety.

    Dense evidence (paths, ids, JSON) is what makes the two disagree: the planner judges the
    prompt to fit while the gate that runs before every model call rejects it, which aborts
    the whole proposal instead of trimming the context.
    """
    import random
    import string

    from agent.cli_backends.budget import check_prompt_budget, prompt_token_limit
    from agent.cli_backends.workspace_mutation.prompts import build_iteration_prompt

    rnd = random.Random(20261017)
    alphabet = string.ascii_letters + string.digits

    def dense(count: int) -> str:
        return "".join(rnd.choice(alphabet) for _ in range(count))

    evidence = [{"tool": "codecompass.plan_context", "output": dense(12_000)} for _ in range(8)]
    prompt = build_iteration_prompt(
        original_prompt="Fixe den Evolver " + dense(2000),
        instructions="Regeln",
        evidence_blocks=evidence,
        iteration=4,
        max_iterations=6,
        max_chars_per_block=12_000,
    )
    # check_prompt_budget returns None for "no budget error" and an error tuple on rejection
    assert check_prompt_budget(prompt, max_tokens=prompt_token_limit("sgpt")) is None
    assert "## Feedback aus vorherigen Iterationen" in prompt
    assert "codecompass.plan_context" in prompt  # evidence survived, it was not dropped entirely


def test_fit_blocks_verified_passes_the_block_through_when_the_gate_accepts():
    blocks = [f"## Schritt {i}\n" + "x" * 500 for i in range(3)]
    fitted = cb.fit_blocks_verified(blocks, budget_chars=10_000,
                                    render=lambda kept: "PREFIX\n" + "\n".join(kept),
                                    allowed=lambda prompt: True,
                                    site="mutation_loop.evidence")
    assert fitted == blocks


def test_fit_blocks_verified_shrinks_the_budget_until_the_gate_accepts():
    blocks = [f"## Schritt {i}\n" + "y" * 5000 for i in range(8)]
    gate_limit = 90_000
    render = lambda kept: "X" * 60_000 + "\n".join(kept)  # noqa: E731
    fitted = cb.fit_blocks_verified(blocks, budget_chars=50_000, render=render,
                                    allowed=lambda prompt: len(prompt) <= gate_limit,
                                    site="mutation_loop.evidence")
    assert fitted and len(render(fitted)) <= gate_limit
    assert any("Schritt" in block for block in fitted)  # real evidence, not only a notice


def test_fit_blocks_verified_returns_no_evidence_when_the_gate_never_accepts():
    fitted = cb.fit_blocks_verified(["a" * 1000], budget_chars=1000, render=lambda kept: "X" * 100,
                                    allowed=lambda prompt: False, site="x", max_rounds=3)
    assert fitted == []
