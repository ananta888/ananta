"""The ``.ananta/last-final-answer.json`` artifact — one builder, two producers.

The mutation loop writes this file natively (its verdict before the proposal
boundary replaces the answer with an executable action). The opencode path has no
loop, so without an equivalent writer ``collect_review`` fails with
``worker_review_answer_missing`` even though the reviewer answered: the answer
lives only in the backend's text.

Both producers share this builder so the schema cannot drift, and the writer only
adds the artifact when it is absent and the answer really carries the verdict
contract the review parses — a missing or unparseable verdict stays missing.

The module also owns the ``gate_review`` completion contract: the reviewer's
``final_answer`` tool is side-effect free, so completion must be refused (for a
bounded number of further model rounds) until the verdict artifact exists —
otherwise a reviewer can finish without ever handing in the answer the review
parses, and the gap only surfaces as ``worker_review_answer_missing`` after the
single-shot review dispatch.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from agent.services.long_context_step_result import output_answer

log = logging.getLogger(__name__)

FINAL_ANSWER_SCHEMA = "ananta_worker_final_answer.v1"
_MAX_ANSWER_CHARS = 16000
REVIEW_ANSWER_ROUNDS_MAX = 3
_REVIEW_ANSWER_ROUNDS_FILE = "review-answer-rounds"


def build_final_answer_artifact(*, task_id: str, answer: str, session_id: str = "") -> dict:
    """Artifact shape ``collect_review`` reads: ``outcome`` plus the verdict text."""
    return {
        "schema": FINAL_ANSWER_SCHEMA,
        "session_id": str(session_id or ""),
        "task_id": str(task_id),
        "outcome": "final_answer",
        "answer": str(answer or "")[:_MAX_ANSWER_CHARS],
        "created_at": time.time(),
    }


def extract_verdict_answer(text: str) -> str | None:
    """The verdict object inside a backend answer, or ``None`` when it is absent.

    Only the *shape* is checked here (``approved`` plus the criteria and finding
    arrays) — ``collect_review`` owns the semantic check, including that the
    criteria array matches the reserved requirements. Writing a text that only
    looks like an answer would trade the current honest ``answer_missing`` for a
    later ``verdict_unparseable``; writing nothing keeps the failure clear.
    """
    source = str(text or "")
    if not source.strip():
        return None
    # Newest complete object first, like the review parser: the verdict is the
    # last thing the backend writes.
    for start in reversed([m.start() for m in re.finditer(r"\{", source)]):
        candidate = _balanced_object(source, start)
        if candidate and _looks_like_verdict(candidate):
            return candidate
    return None


def _looks_like_verdict(candidate: str) -> bool:
    try:
        payload = json.loads(candidate)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    approved = payload.get("approved")
    passed = payload.get("criteria_passed")
    findings = payload.get("findings")
    return (
        isinstance(approved, bool)
        and isinstance(passed, list)
        and all(isinstance(value, bool) for value in passed)
        and isinstance(findings, list)
        and all(isinstance(value, str) for value in findings)
    )


def _balanced_object(text: str, start: int) -> str | None:
    """Brace-balanced object starting at ``start``; ``None`` when unbalanced."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def write_final_answer(workspace: str | Path, *, task_id: str, answer: str, session_id: str = "") -> bool:
    """Persist the verdict artifact when the workspace has none yet.

    Never overwrites: the mutation loop owns the file it already wrote, so this
    only ever fills the gap of a backend that answers without a loop.
    """
    root = Path(workspace)
    artifact_path = root / ".ananta" / "last-final-answer.json"
    if artifact_path.exists():
        return False
    verdict = extract_verdict_answer(answer)
    if verdict is None:
        log.info("final_answer_artifact_skipped_no_verdict task=%s", task_id)
        return False
    artifact = build_final_answer_artifact(task_id=task_id, answer=verdict, session_id=session_id)
    try:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("final_answer_artifact_write_failed task=%s", task_id)
        return False
    return True


def review_answer_present(workspace: str | Path) -> bool:
    """Whether the workspace carries the verdict artifact ``collect_review`` parses."""
    path = Path(workspace) / ".ananta" / "last-final-answer.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and str(payload.get("outcome") or "") == "final_answer"


def review_workspace_dir(task: Any, fallback: str | Path) -> str:
    """The workspace ``collect_review`` reads for a review task.

    ``resolve_workspace_context`` builds the generic ``self-dev-<task>`` directory,
    but the review admission plants ``acceptance-review-<task>`` into the task's
    ``worker_execution_context.workspace.output_dir`` — the artifact and the round
    counter must live exactly there.
    """
    context = task.get("worker_execution_context") if isinstance(task, dict) else getattr(task, "worker_execution_context", None)
    workspace = context.get("workspace") if isinstance(context, dict) else None
    output_dir = str(workspace.get("output_dir") or "").strip() if isinstance(workspace, dict) else ""
    return output_dir or str(fallback)


def review_answer_rounds(workspace: str | Path) -> int:
    """How many model rounds the answer gate refused so far (0 when never).

    Counter file instead of a task column: the review workspace outlives the
    dispatch rounds, and a write failure keeps this at 0, which disables the
    retry — the honest failure path stays the safe default.
    """
    path = Path(workspace) / ".ananta" / _REVIEW_ANSWER_ROUNDS_FILE
    try:
        return max(0, int(str(path.read_text(encoding="utf-8")).strip() or "0"))
    except (OSError, ValueError):
        return 0


def bump_review_answer_round(workspace: str | Path) -> int:
    round_no = review_answer_rounds(workspace) + 1
    root = Path(workspace) / ".ananta"
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / _REVIEW_ANSWER_ROUNDS_FILE).write_text(str(round_no), encoding="utf-8")
    except OSError:  # pragma: no cover - unwritable workspace: retry stays disabled
        log.warning("review_answer_round_counter_write_failed workspace=%s", workspace)
    return round_no


def enforce_review_answer_gate(
    *,
    task_kind: str,
    workspace: str | Path,
    task_id: str,
    output: str,
) -> dict | None:
    """The ``gate_review`` completion contract: no completion without a verdict answer.

    Returns ``{"status": "todo", "reason_code": ..., "round": ...}`` while the verdict
    is missing and model rounds remain — completion must be refused so the task is
    dispatched again. Returns ``None`` once the artifact exists (materialized from a
    ``final_answer`` execution report when the side-effect-free tool produced one)
    or the rounds are exhausted: the task then completes normally and the review
    keeps its honest ``worker_review_answer_missing`` failure.
    """
    if str(task_kind or "").strip().lower() != "gate_review":
        return None
    # The execution output joins the verification command and the tool reports
    # with "---" separators, so the report regex (end-anchored) misses whenever
    # pytest ran after final_answer. Fall back to the raw text: the verdict
    # extractor picks the newest brace-balanced verdict-shaped object anywhere.
    raw = str(output or "")
    answer = output_answer(raw) or raw
    if answer:
        write_final_answer(Path(workspace), task_id=task_id, answer=answer)
    if review_answer_present(workspace):
        return None
    round_no = bump_review_answer_round(workspace)
    if round_no <= REVIEW_ANSWER_ROUNDS_MAX:
        log.info("review_answer_required task=%s round=%s", task_id, round_no)
        return {"status": "todo", "reason_code": "review_answer_required", "round": round_no}
    return None


def review_answer_retry_pending(*, workspace: str | Path) -> bool:
    """Hub side of the gate: keep the item ``pending_review`` for another bounded round.

    The artifact and the round counter are the source of truth: a refused round
    bumped the counter and produced no verdict, while every answered or exhausted
    review keeps the single-shot collect semantics. Task status plumbing is not
    consulted — the worker's own record is already ``completed`` when the gate
    refuses, and that state may sync to the Hub before this decision.
    """
    return 0 < review_answer_rounds(workspace) <= REVIEW_ANSWER_ROUNDS_MAX and not review_answer_present(workspace)
