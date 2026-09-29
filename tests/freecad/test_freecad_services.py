from __future__ import annotations

from agent.services.freecad_export_plan_service import build_export_plan
from agent.services.freecad_macro_execution_service import execute_macro_if_approved
from agent.services.freecad_macro_planning_service import build_macro_plan
from agent.services.freecad_model_inspection_service import inspect_model_context


def test_freecad_model_inspection_service() -> None:
    out = inspect_model_context({"document": {"name": "Doc"}, "objects": [{"type": "Part", "visibility": True}]})
    assert out["document_name"] == "Doc"
    assert out["object_count"] == 1


def test_freecad_export_plan_service() -> None:
    out = build_export_plan(fmt="step", target_path="/tmp/a.step")
    assert out["format"] == "STEP"
    assert out["execution_mode"] == "plan_only"


def test_freecad_macro_planning_service() -> None:
    out = build_macro_plan(objective="reduce weight")
    assert out["mode"] == "dry_run"
    assert out["trusted"] is False


def test_freecad_macro_execution_service() -> None:
    blocked = execute_macro_if_approved(macro_text="print('x')", approved=False, approval_id=None, correlation_id="c1")
    allowed = execute_macro_if_approved(macro_text="print('x')", approved=True, approval_id="A1", correlation_id="c1")
    assert blocked["status"] == "blocked"
    assert allowed["status"] == "completed"
