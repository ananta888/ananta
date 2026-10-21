"""``opencode`` as the mutation loop's brain (L6 variant A).

The loop remains the structure — CodeCompass calls, write policy, evidence
report and the ``final_answer`` kind — and this adapter only swaps the brain
behind the runner contract ``(rc, out, err)``, the same contract
``run_sgpt_command`` and the structured runner honour. Selecting it is
configuration: ``ANANTA_WORKSPACE_MUTATION_LLM_BACKEND=opencode`` lands in
``ananta_worker_workspace_mutation.llm_backend`` and wins over the structured
output opt-in (a brain choice is stronger than an output-format choice).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run_opencode_mutation_llm(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    model: str | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """One loop turn: protocol prompt in, opencode's final answer out.

    The brain runs ``toolless``: opencode's own tools would slow the turn into
    timeouts and let it answer outside the loop's protocol (the CodeCompass
    steps are loop-protocol calls the loop itself executes), so every tool the
    turn may use belongs to the loop, not to the brain.
    """
    from agent.cli_backends.sgpt import run_llm_cli_command

    log.info("mutation_llm_runner=opencode workdir=%s", workdir or "<none>")
    rc, out, err, _backend_used = run_llm_cli_command(
        prompt,
        options=options or [],
        timeout=timeout,
        backend="opencode",
        model=model,
        workdir=workdir,
        routing_policy={"opencode_tool_mode": "toolless"},
    )
    return int(rc), str(out or ""), str(err or "")
