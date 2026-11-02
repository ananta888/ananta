#!/usr/bin/env python3
"""Export self-development review verdicts as labelled decisions for ``laya-train``.

One JSONL row per review: its state, every question of the verdict schema and
the expected answers. The labels are the verdict the Hub accepted and the
evidence registry recorded — the truth the review gate itself settled on, not
a model's opinion. Question phrasings come from the runtime module so a
checkpoint trained on this export is asked exactly the questions it was
trained to answer.

The state is composed compactly on purpose: laya's checkpoints score a few
hundred state tokens, and a training row must not be longer than what the
served request will contain.

Usage:
    python3 scripts/export_laya_review_dataset.py
    laya-train --data artifacts/laya/review-decisions.jsonl \
        --out models/laya-review --base laya-typed-decisions --loss rlcd --device cuda
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.services.self_development.review_verdict_decision import _questions  # noqa: E402

STATE_LIMITS = {"requirements": 400, "files": 200, "analysis": 900}


def _state(requirements: dict, changed_files, analysis: str) -> str:
    criteria = requirements.get("acceptance_criteria") or []
    req = json.dumps({"acceptance_criteria": criteria}, ensure_ascii=False, sort_keys=True)
    files = ", ".join(sorted(changed_files))
    return (
        "Independent acceptance review of one change (decision scoring).\n"
        f"Task requirements (JSON): {req[: STATE_LIMITS['requirements']]}\n"
        f"Changed files: {files[: STATE_LIMITS['files']]}\n"
        f"Reviewer analysis: {str(analysis or '')[: STATE_LIMITS['analysis']]}"
    )


def _analysis_for(item: dict, workspaces: Path) -> str:
    review_task = str(item.get("review_task_id") or "")
    for candidate in (f"self-dev-{review_task}", f"acceptance-review-{review_task}"):
        artifact = workspaces / candidate / ".ananta" / "last-final-answer.json"
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            answer = str(payload.get("answer") or "").strip()
            if answer:
                return answer
        except (OSError, ValueError):
            continue
    return ""


def rows_for_item(item: dict, analysis: str):
    criteria = item.get("acceptance_criteria") or []
    verdict = (item.get("acceptance_review") or {}).get("result") or {}
    passed = verdict.get("criteria_passed") or []
    findings = verdict.get("findings") or []
    if not criteria or len(passed) != len(criteria):
        return
    state = _state({"acceptance_criteria": criteria}, item.get("hashes") or {}, analysis)
    questions = _questions([str(c) for c in criteria])
    labels = [bool(value) for value in passed] + [bool(findings)]
    yield {
        "state": state,
        "questions": {
            question.key: {"type": question.kind, "instructions": question.instructions} for question in questions
        },
        "expected": {question.key: label for question, label in zip(questions, labels)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default="/home/krusty/ananta-data/self-development-control/journal.json")
    parser.add_argument("--workspaces", default="/home/krusty/ananta/project-workspaces")
    parser.add_argument("--out", default="artifacts/laya/review-decisions.jsonl")
    args = parser.parse_args()

    journal = json.loads(Path(args.journal).read_text(encoding="utf-8"))
    workspaces = Path(args.workspaces)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    reviews = 0
    with out.open("w", encoding="utf-8") as stream:
        for item in (journal.get("items") or {}).values():
            if not (item.get("acceptance_review") or {}).get("result"):
                continue
            analysis = _analysis_for(item, workspaces)
            rows = list(rows_for_item(item, analysis))
            if not rows:
                continue
            reviews += 1
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
    print(f"reviews: {reviews} | rows: {written} | out: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
