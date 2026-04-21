from agent.product_benchmark_suite import build_product_benchmark_suite


def test_product_benchmark_suite_covers_core_use_cases_and_scores_to_100():
    suite = build_product_benchmark_suite()

    task_ids = {task["id"] for task in suite["tasks"]}
    assert task_ids == {
        "repo-understanding",
        "bugfix-plan",
        "compose-diagnostics",
        "change-review",
        "guided-first-run",
    }
    assert suite["score_total"] == 100
    criteria = {criterion["id"]: criterion for criterion in suite["criteria"]}
    assert {"task_success", "governance_quality", "block_quality", "reproducibility"}.issubset(criteria)
    assert criteria["governance_quality"]["weight"] >= criteria["time_to_signal"]["weight"]


def test_product_benchmark_suite_defines_comparison_and_release_narrative():
    suite = build_product_benchmark_suite()

    targets = {target["id"]: target for target in suite["comparison_targets"]}
    assert {"openhands-like", "opendevin-like", "openclaw-like"}.issubset(targets)
    assert "governance" in targets["openhands-like"]["expected_contrast"]

    fields = {field["id"] for field in suite["release_narrative_fields"]}
    assert {"headline", "best_signal", "governance_signal", "regression_watch", "evidence_links"}.issubset(fields)
