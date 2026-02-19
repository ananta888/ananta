from __future__ import annotations

from sqlalchemy import inspect, text

from agent.database import engine


STATUS_BACKFILL = {
    "done": "completed",
    "complete": "completed",
    "in-progress": "in_progress",
    "in progress": "in_progress",
    "to-do": "todo",
    "backlog": "todo",
}


def main() -> int:
    inspector = inspect(engine)
    updated = 0
    with engine.begin() as conn:
        for table in ("tasks", "archived_tasks"):
            if not inspector.has_table(table):
                continue
            columns = {col["name"] for col in inspector.get_columns(table)}
            if "status" not in columns:
                continue
            for old_status, canonical_status in STATUS_BACKFILL.items():
                result = conn.execute(
                    text(f"UPDATE {table} SET status = :new_status WHERE lower(trim(status)) = :old_status"),
                    {"new_status": canonical_status, "old_status": old_status},
                )
                updated += int(result.rowcount or 0)
    print(f"status backfill updated rows: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

