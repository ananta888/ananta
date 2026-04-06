from agent.benchmark_quality import evaluate_benchmark_response_quality


def test_planning_response_with_structure_passes_quality_gate():
    result = evaluate_benchmark_response_quality(
        """
        1. Define milestones for the next sprint.
        2. Identify dependencies and risks for the roadmap.
        3. Create a timeline with backlog refinement and review checkpoints.
        """,
        task_kind="planning",
        role_name="planner",
    )

    assert result["passed"] is True
    assert result["score"] >= 55.0


def test_short_generic_response_fails_quality_gate():
    result = evaluate_benchmark_response_quality("Looks good.", task_kind="analysis", role_name="architect")

    assert result["passed"] is False
    assert result["reason"] == "insufficient_quality_evidence"


def test_coding_response_with_code_markers_passes_quality_gate():
    result = evaluate_benchmark_response_quality(
        """
        ```python
        def add(a, b):
            return a + b
        ```

        Add pytest assertions for edge cases and import the module in the test file.
        """,
        task_kind="coding",
        role_name="coder",
    )

    assert result["passed"] is True
    assert result["details"]["task_keyword_hits"] >= 1
