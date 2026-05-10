"""Clean up goals stuck in "planned" state with no running autopilot scope."""
from __future__ import annotations

import time
from sqlmodel import Session, select
from agent.database import engine
from agent.db_models import GoalDB, TaskDB


def cancel_stale_planned_goals(*, before_ts: float | None = None, dry_run: bool = True) -> dict:
    now = time.time()
    cutoff = before_ts or (now - 3600)

    with Session(engine) as session:
        goals = session.exec(
            select(GoalDB).where(GoalDB.status == "planned").where(GoalDB.created_at < cutoff)
        ).all()

        cancelled = 0
        task_count = 0
        for g in goals:
            tasks = session.exec(
                select(TaskDB).where(TaskDB.goal_id == g.id)
            ).all()
            if dry_run:
                print(f"[DRY RUN] Would cancel goal {g.id} ({g.goal[:60]}) with {len(tasks)} tasks")
            else:
                g.status = "cancelled"
                g.updated_at = time.time()
                session.add(g)
                for t in tasks:
                    if t.status in ("planned", "todo"):
                        t.status = "cancelled"
                        t.updated_at = time.time()
                        session.add(t)
                cancelled += 1
                task_count += len(tasks)

        if not dry_run:
            session.commit()

    return {"cancelled_goals": cancelled, "cancelled_tasks": task_count}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--before-hours", type=int, default=1)
    args = parser.parse_args()
    cutoff = time.time() - args.before_hours * 3600
    result = cancel_stale_planned_goals(before_ts=cutoff, dry_run=args.dry_run)
    print(f"{'DRY RUN - ' if args.dry_run else ''}Cancelled: {result['cancelled_goals']} goals, {result['cancelled_tasks']} tasks")
