from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROGRAM = ROOT / "todos/archiv/todo.ai-snake-semantic-media-speech-program.json"
FROZEN_LEGACY_IDS = frozenset(
    [*(f"semrtc-{index:03d}" for index in range(1, 21))]
    + [*(f"speechslow-{index:03d}" for index in range(1, 11))]
    + [*(f"peerspeechsync-{index:03d}" for index in range(1, 13))]
    + [*(f"semspeech-{index:03d}" for index in range(1, 25))]
)


def test_all_frozen_legacy_ids_have_known_nonempty_targets() -> None:
    payload = json.loads(PROGRAM.read_text(encoding="utf-8"))
    coverage = payload["legacy_task_coverage"]
    task_ids = {task["id"] for task in payload["tasks"]}
    assert set(coverage) == FROZEN_LEGACY_IDS
    assert all(isinstance(targets, list) and targets for targets in coverage.values())
    unknown = {target for targets in coverage.values() for target in targets if target not in task_ids}
    assert not unknown
    assert all(len(targets) == len(set(targets)) for targets in coverage.values())


def test_migration_documents_intentional_responsibility_decomposition() -> None:
    text = (ROOT / "docs/planning/semantic-media-speech-todo-migration.md").read_text(encoding="utf-8")
    for concern in ("Alignment", "Relay", "Dataset", "Evaluation", "Negotiation"):
        assert concern in text
    payload = json.loads(PROGRAM.read_text(encoding="utf-8"))
    assert payload["legacy_task_coverage_status"] == "verified_by_ASMP-BASE-003"
    # The migration records the initial planning state; it must not freeze the
    # live delivery state after acceptance evidence has been collected.
    assert "initial new-task states are `todo`" in text
