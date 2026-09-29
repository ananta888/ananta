from __future__ import annotations

from types import SimpleNamespace

from agent.services.planning_telemetry_service import PlanningTelemetryService


def test_build_learning_record_unifies_planning_signals():
    run = SimpleNamespace(
        goal_id="goal-1",
        trace_id="trace-1",
        task_id="task-1",
        mode="new_software_project",
        model_provider="lmstudio",
        model_name="google/gemma-4-e4b",
        planning_profile="lmstudio_laptop",
        prompt_version_id="prompt-v1",
        prompt_language="de",
        parse_mode="parse_failed",
        parse_confidence="low",
        repair_strategy_used="repair_llm_config",
        repair_attempt_count=3,
        validation_success=False,
        generated_task_count=0,
        expected_artifacts_count=2,
        verification_spec_count=1,
        dependency_mode_distribution={"strict": 1},
        materialized_task_ids=["task-a", "task-b"],
        parse_warnings=["truncate"],
        mode_data={"__output_shape__": "partial_json", "__parser_trace__": {"used_step": "json_extract"}, "__truncated__": True},
    )

    record = PlanningTelemetryService().build_learning_record(run)

    assert record["goal_id"] == "goal-1"
    assert record["planning_profile"] == "lmstudio_laptop"
    assert record["parse_mode"] == "parse_failed"
    assert record["model_family"] == "gemma"
    assert record["output_shape"] == "partial_json"
    assert record["observed_output_shape"] == "partial_json"
    assert record["mode_output_shape"] == "partial_json"
    assert record["truncation_flag"] is True
    assert record["materialized_task_count"] == 2
    assert record["parser_trace"] == {"used_step": "json_extract"}


def test_build_learning_record_classifies_raw_output_preview_when_available():
    run = SimpleNamespace(
        goal_id="goal-2",
        trace_id="trace-2",
        task_id="task-2",
        mode="new_software_project",
        model_provider="lmstudio",
        model_name="qwen/qwen-2.5-coder-32b-instruct",
        planning_profile="lmstudio_laptop",
        prompt_version_id="prompt-v2",
        prompt_language="de",
        parse_mode="strict_json",
        parse_confidence="high",
        repair_strategy_used=None,
        repair_attempt_count=0,
        validation_success=True,
        generated_task_count=1,
        expected_artifacts_count=1,
        verification_spec_count=1,
        dependency_mode_distribution={},
        materialized_task_ids=["task-a"],
        parse_warnings=[],
        raw_output_preview='```json\n[{"title":"Task 1","description":"Implement"}]\n```',
        mode_data={},
    )

    record = PlanningTelemetryService().build_learning_record(run)

    assert record["output_shape"] == "json_in_markdown_fence"
    assert record["observed_output_shape"] == "json_in_markdown_fence"
    assert record["mode_output_shape"] is None
