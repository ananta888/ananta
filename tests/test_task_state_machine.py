import pytest
from agent.models import TaskStatus
from agent.services.task_state_machine_service import can_transition_to
from agent.services.task_runtime_service import update_local_task_status
from agent.repository import task_repo

def test_valid_transitions():
    # Linearer Pfad
    assert can_transition_to("todo", "created")[0]
    assert can_transition_to("created", "assigned")[0]
    assert can_transition_to("assigned", "proposing")[0]
    assert can_transition_to("proposing", "in_progress")[0]
    assert can_transition_to("in_progress", "completed")[0]

    # Review Pfad
    assert can_transition_to("in_progress", "waiting_for_review")[0]
    assert can_transition_to("waiting_for_review", "todo")[0] # Action: approve -> todo
    assert can_transition_to("waiting_for_review", "completed")[0]

    # Fehler Pfad
    assert can_transition_to("in_progress", "verification_failed")[0]
    assert can_transition_to("verification_failed", "todo")[0] # Action: retry -> todo

def test_invalid_transitions():
    # Direkter Abschluss ohne Arbeit
    assert not can_transition_to("todo", "completed")[0]

    # Rückschritte ohne explizite Action
    assert not can_transition_to("in_progress", "todo")[0]

    # Terminale Zustände verlassen ohne retry
    assert not can_transition_to("completed", "in_progress")[0]

def test_blocked_by_dependency():
    assert can_transition_to("todo", "blocked_by_dependency")[0]
    assert can_transition_to("in_progress", "blocked_by_dependency")[0]
    assert can_transition_to("blocked_by_dependency", "todo")[0]

def test_update_local_task_status_blocking(app):
    with app.app_context():
        tid = "TEST-SM-1"
        # Initial setzen (force=True oder neu erstellen)
        update_local_task_status(tid, "todo", force=True)

        # Valider Übergang
        update_local_task_status(tid, "created")
        task = task_repo.get_by_id(tid)
        assert task.status == "created"

        # Invalider Übergang (sollte geblockt werden)
        update_local_task_status(tid, "completed")
        task = task_repo.get_by_id(tid)
        assert task.status == "created" # Bleibt auf created

        # Übergang mit force=True (sollte funktionieren)
        update_local_task_status(tid, "completed", force=True)
        task = task_repo.get_by_id(tid)
        assert task.status == "completed"
