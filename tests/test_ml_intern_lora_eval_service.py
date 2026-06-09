"""Tests fuer ml_intern_lora_eval_service (MLLORA-015)."""

import json
import pytest
from pathlib import Path

from agent.services.ml_intern_lora_eval_service import (
    MlInternLoraEvalService,
    get_lora_eval_service,
    _score_todo_json,
    _score_generic,
)


def _write_eval_dataset(tmp_path: Path, records: list[dict], name="eval.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def svc():
    return MlInternLoraEvalService()


def test_dry_run_eval(svc, tmp_path):
    p = _write_eval_dataset(tmp_path, [
        {"instruction": "Was ist 2+2?", "output": "4"},
    ])
    report = svc.evaluate(
        base_model="qwen2.5-coder-7b",
        eval_dataset_path=p,
        dry_run=True,
    )
    assert report.sample_count == 1
    assert report.eval_id.startswith("eval-")
    assert report.eval_dataset_hash != ""


def test_eval_with_dummy_outputs(svc, tmp_path):
    p = _write_eval_dataset(tmp_path, [
        {"instruction": "Erstelle Todo", "output": "{}"},
        {"instruction": "Erklaere Status", "output": "done"},
    ])
    report = svc.evaluate(
        base_model="qwen2.5-coder-7b",
        eval_dataset_path=p,
        base_output_fn=lambda prompt: '{"track": "t", "milestones": [{"id": "M1", "task_ids": [], "status": "todo"}], "tasks": []}',
        adapter_output_fn=lambda prompt: '{"track": "t", "milestones": [{"id": "M1", "task_ids": ["T1"], "status": "todo"}], "tasks": [{"id": "T1", "title": "X", "status": "todo", "priority": "P0", "risk": "low", "acceptance_criteria": ["AC1"], "test_expectations": ["TE1"]}]}',
        scorer_name="todo_json",
        dry_run=False,
    )
    assert report.sample_count == 2


def test_adapter_worse_than_base_not_approved(svc, tmp_path):
    """Adapter mit schlechterem Score soll adapter_better_than_base=False haben."""
    p = _write_eval_dataset(tmp_path, [{"instruction": "Test", "output": "result"}])
    report = svc.evaluate(
        base_model="x",
        eval_dataset_path=p,
        base_output_fn=lambda prompt: '{"track": "good", "milestones": [{"id": "M1", "task_ids": [], "status": "todo"}], "tasks": []}',
        adapter_output_fn=lambda prompt: "not json at all",
        scorer_name="todo_json",
    )
    assert report.adapter_better_than_base is False


def test_invalid_json_output_scored_zero(svc, tmp_path):
    score = _score_todo_json("this is not json")
    assert score["json_valid"] is False
    assert score["total"] == 0.0


def test_valid_todo_json_scored_high():
    output = json.dumps({
        "track": "test",
        "milestones": [{"id": "M1", "title": "M1", "task_ids": ["T1"], "status": "todo"}],
        "tasks": [{
            "id": "T1", "title": "Task 1", "status": "todo",
            "priority": "P0", "risk": "low",
            "acceptance_criteria": ["AC1", "AC2"],
            "test_expectations": ["TE1"],
        }]
    })
    score = _score_todo_json(output)
    assert score["json_valid"] is True
    assert score["has_track"] is True
    assert score["has_tasks"] is True
    assert score["total"] > 0.5


def test_missing_acceptance_criteria_lowers_score():
    full = _score_todo_json(json.dumps({
        "track": "t", "milestones": [], "tasks": [{
            "id": "T1", "title": "X", "status": "todo", "priority": "P0", "risk": "low",
            "acceptance_criteria": ["AC1"], "test_expectations": ["TE1"]
        }]
    }))
    no_ac = _score_todo_json(json.dumps({
        "track": "t", "milestones": [], "tasks": [{
            "id": "T1", "title": "X", "status": "todo", "priority": "P0", "risk": "low",
            "acceptance_criteria": [], "test_expectations": []
        }]
    }))
    assert full["total"] > no_ac["total"]


def test_file_not_found_raises(svc):
    with pytest.raises(Exception):
        svc.evaluate(base_model="x", eval_dataset_path="/nonexistent/eval.jsonl", dry_run=True)


def test_write_report(svc, tmp_path):
    p = _write_eval_dataset(tmp_path, [{"instruction": "Hi", "output": "there"}])
    report = svc.evaluate(base_model="x", eval_dataset_path=p, dry_run=True)
    out = tmp_path / "eval_report.json"
    svc.write_report(report, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["schema"] == "mlintern_eval_report.v1"
    assert "eval_id" in data


def test_singleton():
    s1 = get_lora_eval_service()
    s2 = get_lora_eval_service()
    assert s1 is s2
