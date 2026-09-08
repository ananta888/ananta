"""Progress can only tighten local resource lifetime, never extend Hub authority."""

import pytest

from worker.meet_media.dialog_progress_budget import DialogProgressBudget


def test_startup_and_original_deadline_are_independent_hard_caps():
    assert DialogProgressBudget(7305, 100).deadline == 190
    budget = DialogProgressBudget(101, 100)
    budget.observe(102, 100)
    assert budget.deadline == 101


def test_valid_new_progress_moves_only_resource_deadline_and_duplicates_do_not_extend():
    budget = DialogProgressBudget(500, 100)
    budget.observe(102.5, 100)
    assert budget.deadline == 103
    budget.observe(102.5, 101)
    assert budget.deadline == 103
    budget.observe(103.5, 101)
    assert budget.deadline == 104 and budget.last_progress == 103.5


@pytest.mark.parametrize("value", [None, True, "102", -1, 0, float("nan"), float("inf"), 99, 100, 102.50001])
def test_invalid_stale_or_excessive_progress_closes_budget_irreversibly(value):
    budget = DialogProgressBudget(500, 100)
    with pytest.raises(ValueError, match="progress_invalid"):
        budget.observe(value, 100)
    assert budget.closed
    with pytest.raises(ValueError, match="progress_expired"):
        budget.observe(102, 100)


def test_delayed_packet_cannot_revive_parent_after_its_existing_resource_deadline():
    budget = DialogProgressBudget(500, 100)
    budget.observe(101, 100)
    with pytest.raises(ValueError, match="progress_expired"):
        budget.observe(103, 101.5)
    assert budget.closed


def test_regressing_report_or_clock_is_terminal_not_a_reset():
    budget = DialogProgressBudget(500, 100)
    budget.observe(102.5, 100)
    with pytest.raises(ValueError, match="progress_invalid"):
        budget.observe(102, 101)
    budget = DialogProgressBudget(500, 100)
    budget.require(101)
    with pytest.raises(ValueError, match="progress_expired"):
        budget.require(100)


@pytest.mark.parametrize("now", [None, True, 0, -1, float("nan"), float("inf")])
def test_invalid_clock_cannot_create_budget(now):
    with pytest.raises(ValueError, match="deadline_invalid"):
        DialogProgressBudget(500, now)


@pytest.mark.parametrize("deadline", [None, True, "500", 0, 100, 7305.1, float("nan"), float("inf")])
def test_invalid_original_bound_is_not_repaired(deadline):
    with pytest.raises(ValueError, match="deadline_invalid"):
        DialogProgressBudget(deadline, 100)
