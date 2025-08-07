from __future__ import annotations

from typing import Dict, List, Optional

from src.db import get_conn, init_db


class TaskStore:
    """PostgreSQL backed task store."""

    def __init__(self, *_args, **_kwargs) -> None:
        """Initialize the store. Path arguments are ignored for DB storage."""
        init_db()

    def add_task(self, task: str, agent: Optional[str] = None, template: Optional[str] = None) -> Dict:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO controller.tasks (task, agent, template) VALUES (%s, %s, %s) RETURNING task, agent, template",
            (task, agent, template),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"task": row[0], "agent": row[1], "template": row[2]}

    def next_task(self, agent: Optional[str] = None) -> Optional[Dict]:
        conn = get_conn()
        cur = conn.cursor()
        if agent:
            cur.execute(
                "SELECT id, task, agent, template FROM controller.tasks WHERE agent=%s ORDER BY id LIMIT 1",
                (agent,),
            )
        else:
            cur.execute(
                "SELECT id, task, agent, template FROM controller.tasks ORDER BY id LIMIT 1"
            )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return None
        cur.execute("DELETE FROM controller.tasks WHERE id=%s", (row[0],))
        conn.commit()
        cur.close()
        conn.close()
        return {"task": row[1], "agent": row[2], "template": row[3]}

    def list_tasks(self, agent: Optional[str] = None) -> List[Dict]:
        conn = get_conn()
        cur = conn.cursor()
        if agent:
            cur.execute(
                "SELECT task, agent, template FROM controller.tasks WHERE agent=%s ORDER BY id",
                (agent,),
            )
        else:
            cur.execute(
                "SELECT task, agent, template FROM controller.tasks ORDER BY id"
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {"task": r[0], "agent": r[1], "template": r[2]} for r in rows
        ]
