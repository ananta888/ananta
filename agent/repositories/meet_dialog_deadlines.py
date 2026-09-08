"""Keyset read and existing Hub task-CAS adapter; no independent persistence model."""

import copy

from sqlmodel import Session, select

from agent.db_models.tasks import TaskDB


class SqlDialogDeadlines:
    def __init__(self, engine, *, task_status_cas):
        self.engine = engine
        self.task_status_cas = task_status_cas

    def page(self, after, limit):
        from agent.services.meet_dialog_deadlines import KINDS

        if type(limit) is not int or not 1 <= limit <= 100 or after is not None and not isinstance(after, str):
            raise ValueError("meet_deadline_page_invalid")
        query = select(
            TaskDB.id,
            TaskDB.task_kind,
            TaskDB.tenant_id,
            TaskDB.project_id,
            TaskDB.parent_task_id,
            TaskDB.worker_execution_context,
        ).where(TaskDB.status == "in_progress", TaskDB.task_kind.in_(KINDS))
        if after is not None:
            query = query.where(TaskDB.id > after)
        with Session(self.engine) as session:
            rows = session.exec(query.order_by(TaskDB.id).limit(limit)).all()
            return [
                {
                    "task_id": row[0],
                    "task_kind": row[1],
                    "tenant_id": row[2],
                    "project_id": row[3],
                    "parent_task_id": row[4],
                    "context": copy.deepcopy(row[5]),
                }
                for row in rows
            ]

    def settle(self, candidate, still_expired):
        from agent.services.meet_dialog_deadlines import original_deadline

        original_deadline(candidate)  # This port cannot settle arbitrary task kinds or malformed bindings.
        expected = copy.deepcopy(candidate)
        return self.task_status_cas(
            expected["task_id"],
            "failed",
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: still_expired()
            and row.task_kind == expected["task_kind"]
            and row.tenant_id == expected["tenant_id"]
            and row.project_id == expected["project_id"]
            and row.parent_task_id == expected["parent_task_id"]
            and row.worker_execution_context == expected["context"],
            event_type={
                "meet_dialog_session": "meet_dialog_deadline_expired",
                "meet_audio_receive": "meet_audio_deadline_expired",
                "meet_browser_workspace": "meet_browser_deadline_expired",
            }[expected["task_kind"]],
            event_actor="hub",
            event_details={"reason": "original_deadline_expired"},
        )
