from __future__ import annotations

import json

from agent.services.generated_source_line_policy_service import (
    DECISION_BLOCKED,
    DECISION_FOLLOWUP_REQUIRED,
    DECISION_OK,
    DECISION_WARNING,
    REASON_CROSSED_HARD_LIMIT,
    REASON_EXISTING_OVER_LIMIT_GREW,
    REASON_NEW_FILE_OVER_HARD_LIMIT,
    REASON_OVER_WARNING_THRESHOLD,
    get_generated_source_line_policy_service,
    normalize_generated_source_line_policy_config,
)
from agent.services.task_execution_metrics import (
    reset_source_line_policy_metrics,
    source_line_policy_metrics_snapshot,
)


def _write(path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n" * lines, encoding="utf-8")


def _cfg(mode: str = "block") -> dict:
    cfg = normalize_generated_source_line_policy_config({"enabled": True, "mode": mode})
    cfg["categories"]["production_source"]["hard_max_lines"] = 10
    cfg["categories"]["production_source"]["warn_after_lines"] = 6
    return cfg


def _first(result):
    return result.as_dict()["file_results"][0]


def test_new_production_source_over_hard_is_blocked_in_block_mode(tmp_path):
    _write(tmp_path / "agent" / "big.py", 12)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/big.py"],
        cfg=_cfg("block"),
        baseline={"agent/big.py": None},
    )

    row = _first(result)
    assert result.status == DECISION_BLOCKED
    assert row["reason_code"] == REASON_NEW_FILE_OVER_HARD_LIMIT
    assert row["decision"] == DECISION_BLOCKED


def test_warn_mode_downgrades_block_to_warning(tmp_path):
    _write(tmp_path / "agent" / "big.py", 12)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/big.py"],
        cfg=_cfg("warn"),
        baseline={"agent/big.py": None},
    )

    row = _first(result)
    assert result.status == DECISION_WARNING
    assert row["reason_code"] == REASON_NEW_FILE_OVER_HARD_LIMIT


def test_crossing_hard_limit_requires_followup(tmp_path):
    _write(tmp_path / "agent" / "service.py", 11)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/service.py"],
        cfg=_cfg("block"),
        baseline={"agent/service.py": 10},
    )

    row = _first(result)
    assert result.status == DECISION_FOLLOWUP_REQUIRED
    assert row["reason_code"] == REASON_CROSSED_HARD_LIMIT


def test_existing_over_limit_shrinks_is_not_blocked(tmp_path):
    _write(tmp_path / "agent" / "legacy.py", 11)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/legacy.py"],
        cfg=_cfg("block"),
        baseline={"agent/legacy.py": 15},
    )

    row = _first(result)
    assert result.status == DECISION_WARNING
    assert row["reason_code"] == REASON_EXISTING_OVER_LIMIT_GREW


def test_warning_threshold_and_metrics(tmp_path):
    reset_source_line_policy_metrics()
    _write(tmp_path / "agent" / "medium.py", 7)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/medium.py"],
        cfg=_cfg("block"),
        baseline={"agent/medium.py": None},
    )

    row = _first(result)
    snapshot = source_line_policy_metrics_snapshot()
    assert result.status == DECISION_WARNING
    assert row["reason_code"] == REASON_OVER_WARNING_THRESHOLD
    assert snapshot["source_line_policy_evaluations_total"] == 1
    assert snapshot["source_line_policy_warning_total"] == 1
    assert snapshot["by_category"]["production_source"] == 1


def test_disabled_policy_is_ok_without_file_results(tmp_path):
    _write(tmp_path / "agent" / "big.py", 20)

    result = get_generated_source_line_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["agent/big.py"],
        cfg={"enabled": False},
        baseline={"agent/big.py": None},
    )

    assert result.status == DECISION_OK
    assert result.as_dict()["file_results"] == []


def test_followup_required_can_persist_idempotent_followup(tmp_path):
    cfg = _cfg("block")
    cfg["create_followup_todo"] = True
    _write(tmp_path / "agent" / "service.py", 11)

    for _ in range(2):
        result = get_generated_source_line_policy_service().evaluate_changed_files(
            workspace_dir=tmp_path,
            changed_rel_paths=["agent/service.py"],
            cfg=cfg,
            baseline={"agent/service.py": 10},
            context={"task_id": "task-1", "goal_id": "goal-1"},
        )
        assert result.status == DECISION_FOLLOWUP_REQUIRED

    payload = json.loads((tmp_path / ".ananta" / "source-line-followups.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "generated_source_line_policy_followups.v1"
    assert len(payload["followups"]) == 1
    assert payload["followups"][0]["task_id"] == "task-1"
