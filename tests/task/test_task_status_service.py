from agent.services.task_status_service import expand_task_status_query_values, normalize_task_status


def test_needs_review_alias_maps_to_waiting_for_review():
    assert normalize_task_status("needs_review") == "waiting_for_review"


def test_waiting_for_review_query_values_include_needs_review_alias():
    values = expand_task_status_query_values("waiting_for_review")
    assert "waiting_for_review" in values
    assert "needs_review" in values
